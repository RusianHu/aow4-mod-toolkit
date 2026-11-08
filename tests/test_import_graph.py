from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from aow4mod import import_graph
from aow4mod.core import FormatError


def edge(source, target, status='found', kind='rpk'):
    return {'source':source,'kind':kind,'status':status,'targets':[{'file':target}]}


class ImportGraphTests(unittest.TestCase):
    def test_diamond_has_dependency_first_order_and_no_cycle(self):
        edges=[edge('a.rpk','b.rpk'),edge('a.rpk','c.rpk'),
               edge('b.rpk','d.rpk'),edge('c.rpk','d.rpk')]
        result=import_graph.analyze(edges,{})
        self.assertEqual(result['cycles'],[])
        order=result['dependency_first_order']
        for e in edges:self.assertLess(order.index(e['targets'][0]['file']),order.index(e['source']))

    def test_separate_cycles_and_self_import_have_closed_witnesses(self):
        edges=[edge('a.rpk','b.rpk'),edge('b.rpk','a.rpk'),edge('b.rpk','c.rpk'),
               edge('d.rpk','d.rpk'),edge('x.rpk','y.rpk'),edge('y.rpk','x.rpk')]
        result=import_graph.analyze(edges,{})
        self.assertEqual(len(result['cycles']),3)
        self.assertIsNone(result['dependency_first_order'])
        pairs={(e['source'],e['targets'][0]['file']) for e in edges}
        for cycle in result['cycles']:
            self.assertEqual(cycle['cycle'][0],cycle['cycle'][-1])
            self.assertTrue(all(pair in pairs for pair in zip(cycle['cycle'],cycle['cycle'][1:])))

    def test_long_chain_is_nonrecursive_and_bounds_are_enforced(self):
        edges=[edge(f'{i}.rpk',f'{i+1}.rpk') for i in range(2000)]
        self.assertEqual(import_graph.analyze(edges,{})['nodes'],2001)
        with self.assertRaisesRegex(FormatError,'node bound'):
            import_graph.analyze(edges,{},max_nodes=100)
        with self.assertRaisesRegex(FormatError,'edge bound'):
            import_graph.analyze(edges,{},max_edges=100)

    def test_identical_aliases_are_collapsed(self):
        edges=[edge('a.rpk','b.rpk'),edge('b.rpk','a.rpk'),
               edge('copy/a.rpk','b.rpk'),edge('b.rpk','copy/a.rpk','equivalent_files')]
        result=import_graph.analyze(edges,{'a.rpk':'same','copy/a.rpk':'same','b.rpk':'other'})
        self.assertEqual(result['nodes'],2)
        self.assertEqual(len(result['cycles']),1)
        self.assertEqual(result['cycles'][0]['files'],['a.rpk','b.rpk','copy/a.rpk'])

    def test_ambiguity_is_partial_not_an_invented_cycle_or_safe_order(self):
        result=import_graph.analyze([edge('a.rpk','b.rpk','ambiguous'),edge('b.rpk','a.rpk')],{})
        self.assertEqual(result['cycles'],[])
        self.assertEqual(result['unresolved_declarations'],1)
        self.assertIsNone(result['dependency_first_order'])

    def test_assets_and_acp_override_edges_are_excluded(self):
        result=import_graph.analyze([edge('mod.acp','a.rpk'),edge('a.rpk','mod.acp','found','asset_library')],{})
        self.assertEqual(result['nodes'],0)
        self.assertEqual(result['cycles'],[])


if __name__=='__main__':unittest.main()
