import math
import unittest

import numpy as np

from leakage_simulator.observer_source_index import VirtualSourceIndex
from leakage_simulator.observer_probe import _triangle_intersection


class VirtualSourceIndexTests(unittest.TestCase):
    def test_conservative_candidates_keep_every_exact_hit_in_input_order(self):
        rng=np.random.default_rng(812)
        triangles=rng.uniform(-10,10,(257,3,3))
        index=VirtualSourceIndex(triangles)
        rays=[((0,0,-20),(0,0,1)),((0,0,0),(1,0,0)),((0,0,0),(0,1,0))]
        for _ in range(30):
            direction=rng.normal(size=3); direction/=np.linalg.norm(direction)
            rays.append((tuple(rng.uniform(-12,12,3)),tuple(direction)))
        for origin,direction in rays:
            for maximum in (5.,math.inf):
                candidates=list(index.candidates(origin,direction,1e-8,maximum))
                self.assertEqual(candidates,sorted(set(candidates)))
                exact={i for i,triangle in enumerate(triangles)
                       if (distance:=_triangle_intersection(origin,direction,triangle,1e-8)) is not None and distance<=maximum}
                self.assertTrue(exact.issubset(candidates))

    def test_spatially_separate_triangles_are_skipped_and_edge_hits_survive(self):
        triangles=[((i*10,0,1),(i*10+1,0,1),(i*10,1,1)) for i in range(512)]
        index=VirtualSourceIndex(triangles)
        for point in ((0.,0.,0.),(-1e-11,0.,0.),(1.,0.,0.)):
            candidates=list(index.candidates(point,(0,0,1),1e-8,1.))
            self.assertIn(0,candidates)
            self.assertLess(len(candidates),16)
        self.assertEqual(list(index.candidates((0,0,0),(0,0,-1),1e-8,math.inf)),[])
        self.assertEqual(list(VirtualSourceIndex([]).candidates((0,0,0),(0,0,1),1e-8,math.inf)),[])
