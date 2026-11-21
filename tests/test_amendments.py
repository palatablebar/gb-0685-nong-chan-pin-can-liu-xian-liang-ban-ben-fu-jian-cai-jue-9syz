import json,sqlite3
import pytest
from app.workflow import load,run
from app import store
from app.modules.m8_amend import service
from app.modules.m7_archive.export_out import bundle
from app.errors import DomainError

CLEAN_CSV=lambda:load('clean.csv')

@pytest.fixture
def base_run(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'amend.db'))
    state=run(CLEAN_CSV(),{});store.save(state['run_id'],state)
    return state['run_id']

def _two_amendments(base):
    """演示场景：同一复检报告 00002 的 LOD 补正（采纳）与采样日补正（驳回）。"""
    first=service.adjudicate(base,'AM-2026-001','00002',{'lod':'0.002'})
    second=service.adjudicate(base,'AM-2026-002','00002',{'sampled':'2026-06-15'})
    return first,second

def test_lod_amendment_is_adopted_and_forms_new_version(base_run):
    view=service.adjudicate(base_run,'AM-2026-001','00002',{'lod':'0.002'})
    assert view['after']['run_id']!=base_run
    assert view['current_run_id']==view['after']['run_id']
    ledger=view['ledger']
    assert len(ledger)==1 and ledger[0]['applied']==1 and ledger[0]['reject_code']==''
    # 版本号自增为 3，原始 v1、复检 v2 与补正 v3 均在快照中留作证据
    current=store.get(view['current_run_id'])
    versions=sorted(r['version'] for r in current['rows'] if r['id']=='00002')
    assert versions==['2','3']  # m2 按 (id,version) 去重后保留各历史版本
    calc={c['id']:c for c in current['calculations']}
    assert calc['00002']['code']=='PASS'

def test_sampled_date_amendment_rejected_keeps_current_version(base_run):
    first,second=_two_amendments(base_run)
    # 采样日改动后与原报告 00001 不再同采样日，复检关联校验整份驳回
    assert second['ledger'][-1]['applied']==0
    assert second['ledger'][-1]['reject_code']=='REFERENCE'
    # 当前依据仍是第一份补正形成的版本，既有合格证据不被推翻
    assert second['current_run_id']==first['after']['run_id']
    assert second['current_summary']['business']==first['after']['business']

def test_rejected_amendment_does_not_block_batch_certification(base_run):
    _,second=_two_amendments(base_run)
    cur=store.get(second['current_run_id'])
    assert cur['reconcile']['balanced'] is True and cur['archive']['ready'] is True
    # 封存导出仍然可用，且台账已随证据归档
    export=bundle(cur)
    assert 'AM-2026-001' in export and '运行清单' in export

def test_rerun_same_amendment_is_idempotent_no_new_version(base_run):
    service.adjudicate(base_run,'AM-2026-001','00002',{'lod':'0.002'})
    again=service.adjudicate(base_run,'AM-2026-001','00002',{'lod':'0.002'})
    assert len(again['ledger'])==1
    assert again['current_run_id']==again['ledger'][0]['out_run_id']

def test_same_amendment_id_with_different_payload_conflicts(base_run):
    service.adjudicate(base_run,'AM-2026-001','00002',{'lod':'0.002'})
    with pytest.raises(DomainError) as exc:
        service.adjudicate(base_run,'AM-2026-001','00002',{'lod':'0.003'})
    assert exc.value.status==409 and exc.value.code=='AMEND_CONFLICT'
    # 冲突件未进台账
    assert len(store.list_amendments(base_run))==1

def test_original_report_and_both_amendments_are_retained(base_run):
    _two_amendments(base_run)
    ledger=store.list_amendments(base_run)
    assert [a['amendment_id'] for a in ledger]==['AM-2026-001','AM-2026-002']
    assert {a['applied'] for a in ledger}=={0,1}
    # 原始批次运行与两次裁决输出三个运行快照均可取回
    ids={base_run}|{a['out_run_id'] for a in ledger}
    for rid in ids:assert store.get(rid)['run_id']==rid
    # 被驳回快照内嵌含驳回件的完整台账
    rejected=next(a for a in ledger if not a['applied'])
    snapshot=store.get(rejected['out_run_id'])
    assert {a['amendment_id'] for a in snapshot['amendments']}=={
        'AM-2026-001','AM-2026-002'}

def test_recheck_replays_chain_and_fingerprints_match(base_run):
    _two_amendments(base_run)
    result=service.recheck(base_run)
    assert result['all_match'] is True
    assert [(c['amendment_id'],c['match']) for c in result['checks']]==[
        ('AM-2026-001',True),('AM-2026-002',True)]
    assert result['recomputed_current_run_id']==result['current_run_id']

def test_project_conclusion_follows_current_version(base_run):
    # 00003 原 30ug/kg FAIL；补正为 10ug/kg（0.01<=0.02）应翻转为 PASS
    before=service.ledger(base_run)['current_summary']['business']
    view=service.adjudicate(base_run,'AM-FLIP','00003',{'result':'10'})
    assert before['fail']-view['after']['business']['fail']==1
    assert view['after']['business']['pass']-before['pass']==1
    current=store.get(view['current_run_id'])
    assert next(c for c in current['calculations'] if c['id']=='00003')['code']=='PASS'

def test_batch_grade_and_seal_follow_current_version(tmp_path,monkeypatch):
    monkeypatch.setenv('DB_PATH',str(tmp_path/'g.db'))
    csv=('id,sample_id,batch,product,analyte,method,version,sampled,reported,result,unit,lod,relation,note\n'
         'R1,S1,B009,苹果,P02,GC-MS,1,2026-08-01,2026-08-02,30,ug/kg,1,,x\n'
         'R2,S2,B009,苹果,P02,GC-MS,1,2026-08-01,2026-08-02,30,ug/kg,1,,x\n')
    state=run(csv.encode(),{});store.save(state['run_id'],state);base=state['run_id']
    service.adjudicate(base,'G1','R1',{'result':'10'})
    view=service.adjudicate(base,'G2','R2',{'result':'10'})
    grade=next(g for g in view['after']['grades'] if g['batch']=='B009')
    assert grade['code']=='PASS' and view['after']['seal_ready'] is True
    # 后续驳回补正不改变等级与封存资格
    rejected=service.adjudicate(base,'G3','R1',{'sampled':'2026-06-15'})
    grade_now=next(g for g in rejected['current_summary']['grades'] if g['batch']=='B009')
    assert grade_now['code']=='PASS' and rejected['current_summary']['seal_ready'] is True

def test_chained_amendments_stack_on_current_not_original(base_run):
    service.adjudicate(base_run,'C1','00003',{'result':'10'})
    second=service.adjudicate(base_run,'C2','00003',{'result':'12'})
    current=store.get(second['current_run_id'])
    rows=sorted(r['version'] for r in current['rows'] if r['id']=='00003')
    assert rows==['1','2','3']  # 原始 v1 + 承接当前版本逐份追加的 v2、v3
    calc=next(c for c in current['calculations'] if c['id']=='00003')
    assert calc['code']=='PASS'
    assert service.recheck(base_run)['all_match'] is True

def test_interrupted_write_keeps_old_version(base_run):
    base=base_run
    # 仅让补正写事务的提交失败：事务整体回滚，验证旧版完好、无半写入
    real_connection=store.connection;real_commit=store.commit_amendment
    class FailingConnection:
        def __init__(self,db):self._db=db
        def __enter__(self):return self
        def __exit__(self,*exc):
            if exc[0] is None:raise sqlite3.OperationalError('simulated crash during commit')
            return self._db.__exit__(*exc)
        def __getattr__(self,name):return getattr(self._db,name)
    def failing_commit(entry,run_body):
        store.connection=lambda:FailingConnection(real_connection())
        try:return real_commit(entry,run_body)
        finally:store.connection=real_connection
    store.commit_amendment=failing_commit
    try:
        with pytest.raises(sqlite3.OperationalError):
            service.adjudicate(base,'AM-BOOM','00002',{'lod':'0.002'})
    finally:
        store.commit_amendment=real_commit;store.connection=real_connection
    # 台账与运行快照均无半写入，原始批次运行仍是唯一版本
    assert store.list_amendments(base)==[]
    assert store.run_ids()==[base]
    # 同一补正在中断后重跑仍可正常成版，不产生重复
    view=service.adjudicate(base,'AM-BOOM','00002',{'lod':'0.002'})
    assert len(view['ledger'])==1 and view['current_run_id']!=base

def test_amendment_field_whitelist_and_target_validation(base_run):
    with pytest.raises(DomainError) as exc:
        service.adjudicate(base_run,'X','00002',{'id':'9'})
    assert exc.value.code=='AMEND_FIELDS'
    with pytest.raises(DomainError) as exc:
        service.adjudicate(base_run,'Y','40404',{'lod':'0.01'})
    assert exc.value.code=='AMEND_TARGET'
    with pytest.raises(DomainError) as exc:
        service.adjudicate(base_run,'Z','00002',{})
    assert exc.value.code=='AMEND_FIELDS'

def test_http_workbench_endpoints(client):
    base=client.get('/api/runs').get_json()[0]['id']
    assert client.get('/m8_amend').status_code==200
    a=client.post('/api/m8_amend',json={'run_id':base,'amendment_id':'AM-2026-001',
                                        'target_id':'00002','fields':{'lod':'0.002'}})
    assert a.status_code==200 and a.get_json()['ledger'][0]['applied']==1
    b=client.post('/api/m8_amend',json={'run_id':base,'amendment_id':'AM-2026-002',
                                        'target_id':'00002','fields':{'sampled':'2026-06-15'}})
    assert b.get_json()['ledger'][-1]['reject_code']=='REFERENCE'
    replay=client.post('/api/m8_amend',json={'run_id':base,'amendment_id':'AM-2026-001',
                                             'target_id':'00002','fields':{'lod':'0.002'}})
    assert len(replay.get_json()['ledger'])==2
    conflict=client.post('/api/m8_amend',json={'run_id':base,'amendment_id':'AM-2026-001',
                                               'target_id':'00002','fields':{'lod':'0.009'}})
    assert conflict.status_code==409
    recheck=client.post('/api/m8_amend/recheck',json={'run_id':base})
    assert recheck.status_code==200 and recheck.get_json()['all_match'] is True
    page=client.get('/m8_amend').data.decode()
    assert '补正裁决台账' in page and 'AM-2026-002' in page
    # 当前版本封存导出包含补正台账行
    current=client.get('/api/m8_amend?run_id='+base).get_json()['current_run_id']
    export=client.get('/api/archive/'+current+'/export.csv')
    assert export.status_code==200 and '补正' in export.data.decode('utf-8-sig')

def test_batch_still_certifiable_after_rejection(client):
    # 全部退回会错过出证：驳回补正不得阻断既有合格证据的封存导出
    base=client.get('/api/runs').get_json()[0]['id']
    client.post('/api/m8_amend',json={'run_id':base,'amendment_id':'AM-2026-001',
                                      'target_id':'00002','fields':{'lod':'0.002'}})
    client.post('/api/m8_amend',json={'run_id':base,'amendment_id':'AM-2026-002',
                                      'target_id':'00002','fields':{'sampled':'2026-06-15'}})
    view=client.get('/api/m8_amend?run_id='+base).get_json()
    current=view['current_run_id']
    state=client.get('/api/runs/'+current).get_json()
    assert state['archive']['ready'] is True
    export=client.get('/api/archive/'+current+'/export.csv')
    assert export.status_code==200
    # 权威台账两份补正均在，驳回件可追溯
    ledger=view['ledger']
    assert {a['amendment_id'] for a in ledger}=={'AM-2026-001','AM-2026-002'}
    assert next(a for a in ledger if a['amendment_id']=='AM-2026-002')['reject_code']=='REFERENCE'

def test_workbench_navigation_includes_amendment_module(client):
    from app.config import MODULES,EXTRA_MODULES
    html=client.get('/m8_amend').data.decode()
    for mod,_ in MODULES+EXTRA_MODULES:assert ('/'+mod) in html
    assert 'class="here"' in html
