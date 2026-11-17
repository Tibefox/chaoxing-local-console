import unittest

from scroll_capture import tiles, tile_blocks


class TileTests(unittest.TestCase):
    def test_tiles_cover_long_image_with_overlap_and_no_tiny_tail(self):
        boxes = tiles((900, 2800))
        self.assertEqual(boxes, [(0,0,900,1200),(0,960,900,2160),(0,1920,900,2800)])
        self.assertEqual(tiles((900, 600)), [(0,0,900,600)])

    def test_overlap_keeps_one_copy_without_removing_repeated_content_elsewhere(self):
        boxes = tiles((900, 2000))
        rows = [{'tile':tile,'text':'相同文字','box':[10,y,200,y+30],'confidence':.99}
                for tile,y in [(0,100),(0,1060),(1,1060),(0,1100),(1,1100),(1,1500)]]
        result = tile_blocks(rows, boxes)
        self.assertEqual([b['box'][1] for b in result], [100,1060,1100,1500])
        self.assertTrue(all('tile' not in b for b in result))
