"""Process-local, bounded response cache. Every hit requires a current DB version token."""
from collections import OrderedDict
import json
import gzip
import threading

class CollectionCache:
    def __init__(self, max_bytes=12*1024*1024, max_entries=4):
        self.max_bytes=max_bytes
        self.max_entries=max_entries
        self.entries=OrderedDict()
        self.size=0
        self.lock=threading.Lock()

    def get(self,key):
        with self.lock:
            value=self.entries.get(key)
            if value is None:return None
            self.entries.move_to_end(key)
        # Callers never receive shared mutable public/admin objects.
        return json.loads(gzip.decompress(value))

    def put(self,key,value):
        value=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode()
        if len(value)>32*1024*1024:
            with self.lock:
                previous=self.entries.pop(key,None)
                if previous is not None:self.size-=len(previous)
            return
        value=gzip.compress(value,compresslevel=3,mtime=0)
        with self.lock:
            previous=self.entries.pop(key,None)
            if previous is not None:self.size-=len(previous)
            if len(value)>self.max_bytes:return
            self.entries[key]=value;self.size+=len(value)
            while self.size>self.max_bytes or len(self.entries)>self.max_entries:
                _,old=self.entries.popitem(last=False);self.size-=len(old)

COLLECTION_CACHE=CollectionCache()
