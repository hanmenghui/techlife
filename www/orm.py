'数据库连接池'
#-*-coding:utf-8 -*-

__author__= 'han.han'

import logging;logging.basicConfig(level=logging.INFO)

import asyncio

import aiomysql


def log(sql,ars=()):
	logging.info('SQL:%s args %s' % (sql,ars))

async def create_pool(loop,**kw):
	logging.info('Create database connection pool ...')

	global __pool
	__pool= await aiomysql.create_pool(
		host=kw.get('host','localhost'),
		port=kw.get('port',3306),
		user=kw.get('user'),
		password=kw['password'],
		db=kw['db'],
		charset=kw.get('charset','utf-8'),
		autocommit=kw.get('autocommit',True),
		maxszie=kw.get('maxsize',10),
		minsize=kw.get('minsize',1),
		loop=loop
		)


def create_args_string(num):
    L=[]
    for n in range(num):
        L.append("?")
    return ",".join(L)

async def select(sql,args,size=None):
	log(sql,args)
	global __pool
	async with __pool.get() as conn:
		async with conn.cursor(aiomysql.DictCursor) as cur:
			await cur.execute(sql.replace('?','%s'),args or ())
			if size:
				rs=await cur.fetchmany(size)
			else:
				rs=await cur.fetchall()


		logging.info('rows returned :%s' % len(rs))
		return rs


async def execute(sql,args,autocommit=True):
    log(sql,args)
    global __pool
    async with __pool.get() as conn:
        if not autocommit:
            conn.begin()
        try:
            async with conn.cursor() as cur:
                await cur.execute(sql.replace('?','%s'),args)
                affected=cur.rowcount
        except BaseException as e:
            if not autocommit:
                conn.rollback()
            raise
        return affected





class Field(object):
	def __init__(self,name,column_type,primary_key,default):
		self.name=name
		self.column_type=column_type
		self.primary_key=primary_key
		self.default=default

	def __str__(self):
		return '<%s,%s,%s>' % (self.__class__.__name__,self.column_type,self.name)


class StringField(Field):
	"""docstring for StringField"""
	def __init__(self, name=None,primary_key=False,default=None,ddl='varchar(100)'):
		super().__init__(name,ddl,primary_key,default)

class BooleanField(Field):
	def __init__(self,name=None,primary_key=False,default=False):
		super().__init__(name,'boolean',primary_key,default)


class IntegerField(Field):
	def __init__(self,name=None,primary_key=False,default=0):
		super().__init__(name,'bigint',primary_key,default)

class FloatField(Field):
	def __init__(self,name=None,primary_key=False,default=0.0):
		super().__init__(name,'real',primary_key,default)

class TextField(Field):
	def __init__(self,name=None,default=None):
		super().__init__(name,'text',False,default)


class ModelMetaClass(type):

    def __new__(clas,name,base,attrs):
        if name=='Model':
            return  type.__new__(clas,name,base,attrs)
        tableNmae=attrs.get('__table__',None) or name
        logging.info('Found model : %s (table: %s)'%(name,tableNmae))
        mappings={}
        fields=list()
        primaryKey=None
        for k,v in attrs.items():
            if isinstance(v,Field):
                logging.info('Found mapping:%s ==> %s',(k,v))
                mappings[k]=v
                if v.primary_key:
                    if primaryKey:
                        raise BaseException('Duplicate primary key for field:%s' % k)
                    primaryKey=k
                else:
                    fields.append(k)

        if not primaryKey:
            raise BaseException('Primary key not found.')

        ##实在不懂为什么要弹出
        for k in mappings.keys():
            attrs.pop(k)

        escaped_fields=list(map(lambda f:'`%s`'%f,fields))
        attrs['__mappings__']=mappings
        attrs['__table__']=tableNmae
        attrs['__primary_key__']=primaryKey
        attrs['__fields__']=fields
        attrs['__select__']='select `%s` , %s from `%s` ' % (primaryKey,','.join(fields),tableNmae)
        attrs['__insert__']='insert into %s (%s,%s) values(%s) '%(tableNmae,primaryKey,','.join(fields),create_args_string(len(fields)+1))
        attrs['__update__']='update %s set %s where `%s`=?' %(tableNmae,','.join(map(lambda f:'`%s=?`'% f,fields)),primaryKey)
        attrs['__delete__']='delete from %s where `%s`' % (tableNmae,primaryKey)
        return type.__new__(clas,name,base,attrs)



class Model(dict,metaclass=ModelMetaClass):
    def __init__(self,**kw):
        super().__init__(**kw)

    def __getattr__(self,key):
        try:
            return self[key]
        except KeyError as e:
            raise AttributeError(r"'Model' object has no attribute '%s'" % key)

    def __setattr__(self, key, value):
        self[key]=value


    def getValue(self,key):
        return getattr(self,key,None)



    def getValueOrDefault(self,key):
        value=getattr(self,key,None)
        if value is None:
            field=self.__mappings__[key]
            if field.default is not None:
                value=field.default() if callable(field.default) else field.default
                logging.info('Using default value fro %s:%s'%(key,str(value)) )

        return value



    @classmethod
    async def findAll(cls,where=None,args=None,**kw):
        'find objexts by where clause'
        sql=[cls.__select__]
        if where:
            sql.append('where')
            sql.append(where)
        if args is None:
            args=[]
        orderBy=kw.get('orderBy',None)
        if orderBy:
            sql.append('order by')
            sql.append(orderBy)

        limit=kw.get('limit',None)
        if limit:
            sql.append('limit')
            if isinstance(limit,int):
                sql.append(limit)
            elif isinstance(limit,tuple) and len(limit)==2:
                sql.append('?,?')
                args.extend(limit)
            else:
                raise ValueError('Invalid limit value:%s '% str(limit))

        rs=await select(' '.join(sql),args)
        return [cls(**r) for r in rs]

    @classmethod
    async def find(cls,pk):
        'find object by primary key.'
        if pk is None:
            raise ValueError('Primary key can not be None')
        rs=await select('%s where `%s`=?' %(cls.__select__,cls.__primary_key__),[pk],1)
        if len(rs)==0:
            return None
        return cls(** rs[0])



    async def save(self):
        args=list(map(self.getValueOrDefault,self.__fields__))
        args.append(self.getValueOrDefault(self.__primary_key__))
        rows=await execute(self.__insert__,args)
        if rows!=1:
            logging.warning('Faild to insert record:affected rows:%s'% rows)

    async def update(self):
        args=list(map(self.getValueOrDefault(),self.__fields__))
        args.append(self.getValue(self.__primary__key))
        rows=await  execute(self.__update__,args)


        if rows!=1:
            logging.warning('Failed to updte by primary key :affected rows:%s'% rows)


    async def remove(self):
        args=[self.getValue(self.__primary_key__)]
        rows=await execute(self.__delete__,args)
        if rows!=1:
            logging.warning('Failed to remove by primary key:affected rows:%s'%rows)










































	
