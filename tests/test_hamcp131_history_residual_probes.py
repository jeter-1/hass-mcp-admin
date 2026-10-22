"""Additional M1 checks; original reviewer files remain byte-unchanged."""
import unittest
import test_hamcp131_history_slices as h


class M1RetentionTests(unittest.IsolatedAsyncioTestCase):
    async def exercise(self, mode):
        t = h.HistorySliceTests()
        t.setUp()
        t.seed()
        first = await t.read(end=h.END, limit=3 if mode == 'finished_slice' else 2)
        second = await t.read(end=h.END, limit=1 if mode == 'finished_slice' else 2,
                              cursor=first['next_cursor'])
        self.assertIsNotNone(second['next_cursor'])
        if mode == 'balanced':
            t.recorder.rows = [row for row in t.recorder.rows if row[0] != h.at('01:00:00')]
            t.recorder.add(h.at('00:30:00'), 'late')
        else:
            t.recorder.rows = [(ts, 'changed' if ts == h.at('01:00:00') else state)
                               for ts, state in t.recorder.rows]
        with self.assertRaises(h.GovernanceError) as raised:
            third = await t.read(end=h.END, limit=100, cursor=second['next_cursor'])
            print('UNEXPECTED_ACCEPTANCE', mode, 'complete=', third['complete'],
                  'omissions=', third['omissions'])
        self.assertEqual(raised.exception.code, h.ErrorCode.STALE_CURSOR)

    async def test_in_place_change_two_pages_back_is_stale(self):
        await self.exercise('in_place')

    async def test_balanced_insert_delete_two_pages_back_is_stale(self):
        await self.exercise('balanced')

    async def test_previously_verified_finished_slice_change_is_stale(self):
        await self.exercise('finished_slice')


if __name__ == '__main__':
    unittest.main(verbosity=2)
