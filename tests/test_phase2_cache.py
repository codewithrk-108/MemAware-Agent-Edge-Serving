import unittest

from phase2.cache import EvictionVerificationError, SlotCacheManager, erase_idle_slot


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return b"{}"


class CacheEvictionTests(unittest.TestCase):
    def test_slot_manager_tracks_and_selects_largest_idle_cache(self):
        manager = SlotCacheManager(2)
        manager.assign(0, 10)
        manager.assign(1, 11)
        manager.mark_resident(10, 100)
        manager.mark_resident(11, 200)
        self.assertEqual(manager.preferred_slot(10), 0)
        self.assertEqual(manager.eviction_slot({0, 1}), 1)
        self.assertEqual(manager.forget_slot(1), (11, 200))
        manager.assign(1, 12)
        self.assertEqual(manager.owner(1), 12)

    def test_slot_manager_does_not_evict_protected_task(self):
        manager = SlotCacheManager(1)
        manager.assign(0, 10)
        manager.mark_resident(10, 100)
        self.assertIsNone(manager.eviction_slot({0}, protected_task=10))

    def test_slot_manager_does_not_evict_grace_protected_slot(self):
        manager = SlotCacheManager(2)
        manager.assign(0, 10)
        manager.assign(1, 11)
        manager.mark_resident(10, 8)
        manager.mark_resident(11, 4)
        self.assertEqual(manager.eviction_slot({0, 1}, protected_slots={0}), 1)

    def test_active_slot_cannot_be_erased(self):
        with self.assertRaisesRegex(ValueError, "active slot"):
            erase_idle_slot("http://test", 2, {2})

    def test_erase_requires_observed_release(self):
        samples = iter([{"used_cells": 100}, {"used_cells": 100}])
        with self.assertRaisesRegex(EvictionVerificationError, "not observed"):
            erase_idle_slot("http://test", 2, set(), sample=lambda _: next(samples),
                            opener=lambda *args, **kwargs: FakeResponse(), verification_attempts=1)

    def test_reports_reclaimed_cells(self):
        samples = iter([{"used_cells": 100}, {"used_cells": 60}])
        result = erase_idle_slot("http://test", 2, set(), sample=lambda _: next(samples),
                                 opener=lambda *args, **kwargs: FakeResponse())
        self.assertEqual(result.reclaimed_cells, 40)


if __name__ == "__main__":
    unittest.main()
