import unittest

from scripts.direct_module_replay import replay_closed_modules


def module(name, imports=(), facts=()):
    rows = [{'schema': 'theseus.lean-semantic-fact.v1', 'module': name,
             'full_name': n, 'kind': k, 'type_dependencies': t, 'value_dependencies': v}
            for n, k, t, v in facts]
    return {'schema': 'theseus.lean-module-metadata.v1', 'module': name,
            'is_module': False, 'imports': [{'module': i, 'is_exported': True, 'import_all': False, 'is_meta': False} for i in imports],
            'constant_names': [r['full_name'] for r in rows], 'olean_extra_const_names': [], 'fact_count': len(rows)}, rows


class ClosedModuleReplayTests(unittest.TestCase):
    def test_replays_order_and_preserves_both_equal_observed_occurrences(self):
        base = module('Base', facts=[('P', 'axiom', [], []), ('p', 'axiom', ['P'], [])])
        left = module('Left', ['Base'], [('shared', 'theorem', ['P'], ['p'])])
        right = module('Right', ['Base'], [('shared', 'theorem', ['P'], ['p'])])
        root = module('Root', ['Right', 'Left'])
        result = replay_closed_modules([left, root, right, base], ['Root'])
        self.assertEqual(result.module_order, ['Base', 'Right', 'Left', 'Root'])
        self.assertEqual(result.occurrences['shared'], ['Right', 'Left'])
        self.assertEqual(result.graph['nodes'], [
            {'fullName': 'Base.P', 'name': 'P', 'kind': 'axiom', 'module': 'Base'},
            {'fullName': 'Base.p', 'name': 'p', 'kind': 'axiom', 'module': 'Base'},
            {'fullName': 'Right.shared', 'name': 'shared', 'kind': 'thm', 'module': 'Right'},
        ])
        self.assertEqual(result.graph['edges'], [
            {'source': 'Base.P', 'target': 'Base.p', 'kind': 'type'},
            {'source': 'Base.P', 'target': 'Right.shared', 'kind': 'type'},
            {'source': 'Base.p', 'target': 'Right.shared', 'kind': 'value'},
        ])

    def test_conflicting_proof_dependencies_do_not_become_union_or_first_arrival(self):
        base = module('Base', facts=[('P', 'axiom', [], []), ('p', 'axiom', ['P'], []), ('q', 'axiom', ['P'], [])])
        left = module('Left', ['Base'], [('shared', 'theorem', ['P'], ['p'])])
        right = module('Right', ['Base'], [('shared', 'theorem', ['P'], ['q'])])
        with self.assertRaisesRegex(ValueError, 'conflicting observed.*shared'):
            replay_closed_modules([base, left, right], ['Left', 'Right'])

    def test_missing_import_rejected_even_if_no_facts(self):
        with self.assertRaisesRegex(ValueError, 'missing module.*Absent'):
            replay_closed_modules([module('Root', ['Absent'])], ['Root'])

    def test_cycle_rejected(self):
        with self.assertRaisesRegex(ValueError, 'cycle'):
            replay_closed_modules([module('A', ['B']), module('B', ['A'])], ['A'])

    def test_unreachable_input_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unreachable'):
            replay_closed_modules([module('Root'), module('Other')], ['Root'])

    def test_duplicate_module_and_fact_rejected(self):
        with self.assertRaisesRegex(ValueError, 'duplicate module'):
            replay_closed_modules([module('Root'), module('Root')], ['Root'])
        item = module('Root', facts=[('p', 'axiom', [], [])])
        item[1].append(item[1][0])
        item[0]['fact_count'] = 2
        with self.assertRaisesRegex(ValueError, 'duplicate fact'):
            replay_closed_modules([item], ['Root'])

    def test_missing_dependency_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unknown dependency.*lost'):
            replay_closed_modules([module('Root', facts=[('p', 'theorem', [], ['lost'])])], ['Root'])

    def test_wrong_module_and_count_rejected(self):
        item = module('Root', facts=[('p', 'axiom', [], [])])
        item[1][0]['module'] = 'Elsewhere'
        with self.assertRaisesRegex(ValueError, 'module mismatch'):
            replay_closed_modules([item], ['Root'])
        item = module('Root', facts=[('p', 'axiom', [], [])])
        item[0]['fact_count'] = 0
        with self.assertRaisesRegex(ValueError, 'fact count'):
            replay_closed_modules([item], ['Root'])

    def test_unrepresented_constants_and_extra_names_rejected_in_strict_pilot(self):
        item = module('Root')
        item[0]['constant_names'] = ['hidden']
        with self.assertRaisesRegex(ValueError, 'unrepresented constants'):
            replay_closed_modules([item], ['Root'])
        item = module('Root')
        item[0]['olean_extra_const_names'] = ['extra']
        with self.assertRaisesRegex(ValueError, 'extra constant'):
            replay_closed_modules([item], ['Root'])

    def test_split_module_not_claimed_by_closed_legacy_pilot(self):
        item = module('Root')
        item[0]['is_module'] = True
        with self.assertRaisesRegex(ValueError, 'split module'):
            replay_closed_modules([item], ['Root'])

    def test_synthetic_display_id_collision_rejected(self):
        base = module('Base', facts=[('p', 'axiom', [], [])])
        root = module('Root', ['Base'], [('Base.p', 'axiom', [], [])])
        with self.assertRaisesRegex(ValueError, 'colliding graph identities'):
            replay_closed_modules([base, root], ['Root'])

    def test_nondefault_import_flags_rejected(self):
        root = module('Root', ['Base'])
        root[0]['imports'][0]['import_all'] = True
        with self.assertRaisesRegex(ValueError, 'unsupported import metadata'):
            replay_closed_modules([module('Base'), root], ['Root'])
