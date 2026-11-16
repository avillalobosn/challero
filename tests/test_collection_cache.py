import unittest
from collection_cache import CollectionCache

class CollectionCacheTests(unittest.TestCase):
    def test_namespace_scope_version_and_mutation_isolation(self):
        cache=CollectionCache()
        public=('database-a',False,'v1');admin=('database-a',True,'v1')
        cache.put(public,{'rows':[{'name':'Public'}]})
        cache.put(admin,{'rows':[{'name':'Secret','purchase_price':42}]})
        copy=cache.get(public);copy['rows'].clear()
        self.assertEqual(cache.get(public)['rows'][0]['name'],'Public')
        self.assertNotIn('Secret',str(cache.get(public)))
        self.assertIsNone(cache.get(('database-a',False,'v2')))
        self.assertIsNone(cache.get(('database-b',True,'v1')))
        self.assertEqual(cache.get(admin)['rows'][0]['purchase_price'],42)
    def test_lru_and_memory_bound(self):
        cache=CollectionCache(max_bytes=150,max_entries=2)
        cache.put('a',{'row':'a'});cache.put('b',{'row':'b'})
        cache.get('a');cache.put('c',{'row':'c'})
        self.assertIsNone(cache.get('b'))
        self.assertLessEqual(cache.size,150)
        cache.put('large',{'rows':list(range(1000))})
        self.assertIsNone(cache.get('large'))
        self.assertLessEqual(cache.size,150)
