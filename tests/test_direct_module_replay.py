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
        self.assertIsNone(result.selected_modules['shared'])
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


    def test_verified_theorem_selection_separates_attribution_from_proof(self):
        base = module('Base', facts=[('P', 'axiom', [], []), ('p', 'axiom', ['P'], []), ('q', 'axiom', ['P'], [])])
        left = module('Left', ['Base'], [('shared', 'theorem', ['P'], ['p'])])
        right = module('Right', ['Base'], [('shared', 'theorem', ['P'], ['q'])])
        calls = []
        def verify(previous, incoming):
            calls.append((previous['module'], incoming['module']))
        result = replay_closed_modules([base, left, right], ['Left', 'Right'], verify_theorem=verify)
        self.assertEqual(calls, [('Left', 'Right')])
        self.assertEqual(result.occurrences['shared'], ['Left', 'Right'])
        self.assertEqual(result.selected_modules['shared'], 'Right')
        self.assertIn({'fullName': 'Left.shared', 'name': 'shared', 'kind': 'thm', 'module': 'Left'}, result.graph['nodes'])
        self.assertIn({'source': 'Base.q', 'target': 'Left.shared', 'kind': 'value'}, result.graph['edges'])
        self.assertNotIn({'source': 'Base.p', 'target': 'Left.shared', 'kind': 'value'}, result.graph['edges'])

    def test_split_requires_explicit_private_mode_and_ir_inventory(self):
        item = module('Root')
        item[0]['is_module'] = True
        with self.assertRaisesRegex(ValueError, 'IR inventory'):
            replay_closed_modules([item], ['Root'], private_split=True)
        item[0]['ir_extra_const_names'] = []
        result = replay_closed_modules([item], ['Root'], private_split=True)
        self.assertEqual(result.module_order, ['Root'])
        item[0]['ir_extra_const_names'] = ['extra']
        with self.assertRaisesRegex(ValueError, 'IR extra constant'):
            replay_closed_modules([item], ['Root'], private_split=True)

    def test_split_duplicate_requires_structural_verification_even_if_signatures_match(self):
        left = module('Left', facts=[('shared', 'theorem', [], [])])
        right = module('Right', facts=[('shared', 'theorem', [], [])])
        for meta, _ in (left, right):
            meta['is_module'] = True
            meta['ir_extra_const_names'] = []
        with self.assertRaisesRegex(ValueError, 'requires theorem verification'):
            replay_closed_modules([left, right], ['Left', 'Right'], private_split=True)

    def test_private_inventory_tracks_filtered_dependencies_and_ir_attribution(self):
        base = module('Base', facts=[('P', 'axiom', [], [])])
        base[0].update(is_module=True, internal_constant_names=['_hidden'],
                       constant_names=['P', '_hidden'], ir_extra_const_names=['later', 'irOnly'])
        root = module('Root', ['Base'], [('later', 'theorem', ['P'], ['_hidden', 'irOnly'])])
        root[0].update(is_module=True, internal_constant_names=[], ir_extra_const_names=[])
        root[0]['imports'][0].update(is_exported=False, is_meta=True, import_all=True)
        result = replay_closed_modules([root, base], ['Root'], private_split=True)
        self.assertEqual(result.name_attribution, {'P': 'Base', '_hidden': 'Base', 'later': 'Base', 'irOnly': 'Base'})
        self.assertEqual(result.occurrences['later'], ['Root'])
        self.assertEqual(result.selected_modules['later'], 'Root')
        self.assertIn({'fullName': 'Base.later', 'name': 'later', 'kind': 'thm', 'module': 'Base'}, result.graph['nodes'])
        self.assertEqual(result.omitted_dependencies, [('later', '_hidden', 'value', 'internal'), ('later', 'irOnly', 'value', 'ir_only')])
        self.assertEqual(result.graph['edges'], [{'source': 'Base.P', 'target': 'Base.later', 'kind': 'type'}])

    def test_inventory_cannot_hide_unclassified_missing_fact(self):
        item = module('Root')
        item[0].update(is_module=True, internal_constant_names=[], constant_names=['lost'], ir_extra_const_names=[])
        with self.assertRaisesRegex(ValueError, 'unrepresented constants'):
            replay_closed_modules([item], ['Root'], private_split=True)

    def test_internal_and_visible_classification_must_not_conflict(self):
        base = module('Base')
        base[0].update(is_module=True, internal_constant_names=['x'], constant_names=['x'], ir_extra_const_names=[])
        root = module('Root', ['Base'], [('x', 'axiom', [], [])])
        root[0].update(is_module=True, internal_constant_names=[], ir_extra_const_names=[])
        with self.assertRaisesRegex(ValueError, 'conflicting internal classification'):
            replay_closed_modules([root, base], ['Root'], private_split=True)

    def test_legacy_attribution_uses_olean_extra_not_ir(self):
        item = module('Root', facts=[('p', 'theorem', [], ['legacyExtra'])])
        item[0].update(internal_constant_names=[], olean_extra_const_names=['legacyExtra'], ir_extra_const_names=['notUsed'])
        result = replay_closed_modules([item], ['Root'], private_split=True)
        self.assertEqual(result.name_attribution, {'p': 'Root', 'legacyExtra': 'Root'})
        self.assertEqual(result.omitted_dependencies, [('p', 'legacyExtra', 'value', 'ir_only')])

    def test_quoted_name_uses_lean_short_name_not_dot_splitting(self):
        item = module('Root', facts=[('N.«a.b»', 'axiom', [], [])])
        with self.assertRaisesRegex(ValueError, 'quoted name'):
            replay_closed_modules([item], ['Root'])
        item[0].update(internal_constant_names=[], quoted_short_names={'N.«a.b»': '«a.b»'})
        result = replay_closed_modules([item], ['Root'], private_split=True)
        self.assertEqual(result.graph['nodes'], [{'fullName': 'N.«a.b»', 'name': '«a.b»', 'kind': 'axiom', 'module': 'Root'}])
        item[0]['quoted_short_names'] = {'other': 'wrong'}
        with self.assertRaisesRegex(ValueError, 'quoted name'):
            replay_closed_modules([item], ['Root'], private_split=True)
