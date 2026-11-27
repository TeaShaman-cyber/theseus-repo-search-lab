import unittest
from types import SimpleNamespace as NS

from theseus_repo_search.corpus_coverage import coverage_report, validate_live_witness


def node(name, module, path):
    return NS(id='lean:'+name, module=module, source_path=path)


def fixture():
    raw = {'nodes': [
        {'fullName': 'M.a', 'module': 'M.Entry'},
        {'fullName': 'M.b', 'module': 'M.Dependency'},
        {'fullName': 'M.c', 'module': 'M.Dependency'},
    ], 'edges': [{}, {}]}
    nodes = [node('M.a','M.Entry','M/Entry.lean'),
             node('M.b','M.Dependency','M/Dependency.lean'),
             node('M.c','M.Dependency','M/Dependency.lean')]
    edges = [NS(source_id='lean:M.a', target_id='lean:M.b'),
             NS(source_id='lean:M.b', target_id='lean:M.c')]
    sources = [NS(source_path=p) for p in ('M/Entry.lean','M/Dependency.lean','M/Other.lean')]
    plan = {'purpose':'dependency structure', 'producer_kind':'lean-dep-viz',
            'excluded_raw_modules':{}, 'excluded_raw_declarations':{}, 'lexical_boundary':'Other is context only',
            'required_modules':['M.Entry','M.Dependency'],
            'witnesses':[{'declaration':'M.a','source_path':'M/Entry.lean',
                          'direction':'forward','neighbor':'M.b'}]}
    return raw,nodes,edges,sources,plan


class CorpusCoverageTests(unittest.TestCase):
    def test_complete_cone_records_lexical_boundary(self):
        raw,n,e,s,p=fixture()
        r=coverage_report(raw,n,e,s,p,'lean-dep-viz')
        self.assertEqual(r['raw_nodes'],3)
        self.assertEqual(r['graph_nodes'],3)
        self.assertEqual(r['lexical_only_paths'],['M/Other.lean'])

    def test_matrix_style_scope_loss_is_rejected_even_if_remaining_graph_is_valid(self):
        raw,n,_e,s,p=fixture()
        with self.assertRaisesRegex(ValueError,'required module|unexplained'):
            coverage_report(raw,n[:1],[],s,p,'lean-dep-viz')

    def test_bounded_slice_requires_explicit_excluded_modules(self):
        raw,n,_e,s,p=fixture()
        p['required_modules']=['M.Entry']
        p['witnesses'][0]['neighbor']=None
        with self.assertRaisesRegex(ValueError,'unexplained'):
            coverage_report(raw,n[:1],[],s,p,'lean-dep-viz')
        p['excluded_raw_modules']={'M.Dependency':'unrelated reference slice'}
        self.assertEqual(coverage_report(raw,n[:1],[],s,p,'lean-dep-viz')['excluded_raw_nodes'],2)

    def test_individual_source_binding_exclusions_must_be_exact(self):
        raw,n,e,s,p=fixture()
        with self.assertRaisesRegex(ValueError,'unexplained declaration'):
            coverage_report(raw,n[:2],e[:1],s,p,'lean-dep-viz')
        p['excluded_raw_declarations']={'lean:M.c':'no unique source binding'}
        self.assertEqual(coverage_report(raw,n[:2],e[:1],s,p,'lean-dep-viz')['excluded_raw_nodes'],1)
        p['excluded_raw_declarations']['lean:M.b']='stale exclusion'
        with self.assertRaisesRegex(ValueError,'unexplained declaration'):
            coverage_report(raw,n[:2],e[:1],s,p,'lean-dep-viz')

    def test_missing_research_witness_cannot_be_replaced_by_node_counts(self):
        raw,n,e,s,p=fixture()
        p['witnesses'][0]['declaration']='M.absent'
        with self.assertRaisesRegex(ValueError,'witness'):
            coverage_report(raw,n,e,s,p,'lean-dep-viz')

    def test_witness_requires_actual_direct_edge(self):
        raw,n,e,s,p=fixture()
        p['witnesses'][0]['neighbor']='M.c'
        with self.assertRaisesRegex(ValueError,'witness'):
            coverage_report(raw,n,e,s,p,'lean-dep-viz')

    def test_wrong_backend_or_missing_graph_seed_rejected(self):
        _raw,_n,_e,_s,p=fixture()
        w=p['witnesses'][0]
        hit={'source_id':'m','artifact_identity':'a','declaration_id':'lean:M.a','source_path':'M/Entry.lean'}
        g={'source_id':'m','artifact_identity':'a','root':'lean:M.a','backend':'kusto',
           'found':True,'status':'FOUND','projection_nodes':3,'projection_edges':2,'direction':'forward',
           'depth':1,'nodes':[{'id':'lean:M.b','depth':1}]}
        result={'backend':'turso+kusto','graph_status':'FOUND','searched_corpora':1,'unavailable_corpora':0,
                'hits':[hit],'graphs':[g],'graph_unavailable':[]}
        validate_live_witness(result,w,'m','a',3,2)
        for bad in ({**result,'backend':'turso'},{**result,'graphs':[]},
                    {**result,'graphs':[{**g,'nodes':[]}]}):
            with self.assertRaises(ValueError):
                validate_live_witness(bad,w,'m','a',3,2)


class SelectionReceiptTests(unittest.TestCase):
    def test_added_and_replaced_entries_are_checked_not_unchanged_ones(self):
        from scripts.check_corpus_selection import changed_corpora
        a,b,c = (NS(source_id=i, artifact_identity='old') for i in ('a','b','c'))
        replaced = NS(source_id='b', artifact_identity='new')
        base = NS(release_repository='r',corpora=[a,b])
        candidate = NS(release_repository='r',corpora=[a,replaced,c])
        self.assertEqual(changed_corpora(base,candidate),[replaced,c])

    def test_receipt_cannot_be_reused_for_replaced_artifact_or_changed_plan(self):
        from scripts.check_corpus_selection import SCHEMA, digest, validate_receipt
        from theseus_repo_search.accepted_catalog import (
            AcceptedCatalog,
            AcceptedCorpus,
            accepted_catalog_sha256,
        )
        item = AcceptedCorpus('m','tag','p','r','producer/sources/m.json','a'*64,'b'*64)
        catalog = AcceptedCatalog('schema','owner/repo',(item,))
        _raw,_n,_e,_s,plan = fixture()
        receipt = {'schema':SCHEMA,'source_id':'m','artifact_identity':'0'*64,
                   'fingerprint_sha256':item.fingerprint_sha256,
                   'catalog_sha256':accepted_catalog_sha256(catalog),
                   'plan':plan,'plan_sha256':digest(plan),'witnesses':[]}
        with self.assertRaisesRegex(ValueError,'identity or plan'):
            validate_receipt(receipt,item,catalog,{'graph_nodes':3,'graph_edges':2})
        receipt['artifact_identity']=item.artifact_identity
        receipt['plan']['purpose']='changed after witness capture'
        with self.assertRaisesRegex(ValueError,'identity or plan'):
            validate_receipt(receipt,item,catalog,{'graph_nodes':3,'graph_edges':2})
        receipt['plan_sha256']=digest(plan)
        with self.assertRaisesRegex(ValueError,'missing live witnesses'):
            validate_receipt(receipt,item,catalog,{'graph_nodes':3,'graph_edges':2})


class HistoricalReleaseLocatorTests(unittest.TestCase):
    def test_historical_longer_exact_identity_prefix_is_valid(self):
        from scripts.check_corpus_selection import validate_release_tag
        item = NS(release_tag="accepted-artifact/anthropic-fermats-last-theorem/21b6b292cb3160fa3",
                  artifact_identity="21b6b292cb3160fa33a962ad72e1890691250279e016c251150cdb2f84ce62c3")
        validate_release_tag(item)
        item.release_tag = "accepted-artifact/anthropic-fermats-last-theorem/21b6b292cb3160fa4"
        with self.assertRaises(ValueError):
            validate_release_tag(item)


class LiveSourceRevisionTests(unittest.TestCase):
    def test_legacy_and_current_fields_must_bind_same_exact_revision(self):
        from scripts.check_corpus_selection import validate_source_revision
        for row in ({"source_commit":"abc"}, {"source_revision":"abc"},
                    {"source_commit":"abc","source_revision":"abc"}):
            validate_source_revision(row,"abc")
        for row in ({}, {"source_commit":"wrong"}, {"source_revision":"wrong"},
                    {"source_commit":"wrong","source_revision":"abc"}):
            with self.assertRaises(ValueError):
                validate_source_revision(row,"abc")
