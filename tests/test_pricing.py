import copy,time,unittest
import server as app

class PricingTests(unittest.TestCase):
    def setUp(self):
        self.card={'card_sets':[{'set_code':'ABC-EN001','set_rarity':'Super Rare','set_rarity_code':'(SR)','set_price':'12.34'}],
                   'card_prices':[{'tcgplayer_price':'2.10','cardmarket_price':'3.20','ebay_price':'0','amazon_price':'NaN'}]}
        self.row={'Card Number':'ABC-EN001','Rarity':'SR'}
    def test_exact_set_and_rarity(self):
        ref=app.reference_prices(self.row,app.pricing_metadata(self.card,time.time()))
        self.assertEqual(ref['match'],'matched');self.assertEqual(ref['set_price'],'12.34');self.assertFalse(ref['stale'])
        self.assertEqual([(x['currency'],x['amount']) for x in ref['vendors']],[('USD','2.10'),('EUR','3.20')])
    def test_no_fallback_to_cheapest_version(self):
        for row in [{'Card Number':'OTHER-EN001','Rarity':'SR'},{'Card Number':'ABC-EN001','Rarity':'UR'},{'Card Number':'','Rarity':'SR'}]:
            ref=app.reference_prices(row,app.pricing_metadata(self.card,time.time()))
            self.assertEqual(ref['match'],'unmatched');self.assertIsNone(ref['set_price'])
    def test_duplicate_candidates_are_ambiguous(self):
        self.card['card_sets'].append(dict(self.card['card_sets'][0],set_price='999.99'))
        ref=app.reference_prices(self.row,app.pricing_metadata(self.card,time.time()))
        self.assertEqual(ref['match'],'ambiguous');self.assertIsNone(ref['set_price'])
    def test_unavailable_and_stale(self):
        self.card['card_sets'][0]['set_price']='0'
        ref=app.reference_prices(self.row,app.pricing_metadata(self.card,time.time()-3*86400))
        self.assertEqual(ref['match'],'unpriced');self.assertIsNone(ref['set_price']);self.assertTrue(ref['stale'])
        for value in [None,'',True,'NaN','Infinity','-1','0','1e100','no price']:
            self.assertIsNone(app.source_amount(value),value)
    def test_public_price_requires_both_inputs(self):
        self.assertIsNone(app.public_price(dict(self.row, LOW='5.00'),{'currency':'USD'}))
        self.assertEqual(app.public_price(dict(self.row, LOW='1.00', MARKET='1.01'),{'currency':'USD'}),'1.01')

if __name__=='__main__': unittest.main()
