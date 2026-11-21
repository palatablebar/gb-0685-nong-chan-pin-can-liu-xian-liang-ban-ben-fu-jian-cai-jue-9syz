import base64,csv,hashlib,io,json
from decimal import Decimal
from app.errors import DomainError
from app.workflow import run
from app.modules.m1_ingest.decode import decode
from app import store

# 版本号由台账按报告自增管理，id 标识报告本身，均不接受补正直接改写。
AMENDABLE={'sample_id','batch','product','analyte','method','sampled','reported',
           'result','unit','lod','relation','note'}

def _fingerprint(amendment_id,target_id,fields):
    raw=json.dumps({'amendment_id':amendment_id,'target_id':target_id,'fields':fields},
                   sort_keys=True,ensure_ascii=False,separators=(',',':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()

def _base(run_id):
    body=store.get(run_id)
    if not body.get('source_b64'):raise DomainError('AMEND_BASE','该运行缺少原始报告，无法补正')
    params=body.get('manifest',{}).get('parameters',{}) or {}
    # 补正按到达顺序作用于当前有效版本：后续补正叠加在最新成版快照上，而非原始批次。
    current_out=next((a['out_run_id'] for a in reversed(store.list_amendments(run_id)) if a['applied']),None)
    if current_out:
        current=store.get(current_out)
        if current.get('source_b64'):return current,base64.b64decode(current['source_b64']),params
    return body,base64.b64decode(body['source_b64']),params

def _patch_content(content,target_id,fields):
    """在原始CSV字节上叠加一份补正，生成追加新版本行后的完整CSV。

    只追加、不改写历史行：历史版本留作采信证据，由 M5 规则保证不重复参与判定。
    """
    text,_=decode(content)
    reader=csv.reader(io.StringIO(text,newline=''))
    try:header=next(reader)
    except StopIteration:raise DomainError('HEADER','无法读取原始报告表头')
    rows=list(reader);idx={name:i for i,name in enumerate(header)}
    if 'id' not in idx or 'version' not in idx:raise DomainError('HEADER','原始报告缺少标识列')
    matches=[r for r in rows if len(r)>idx['id'] and r[idx['id']]==target_id]
    if not matches:raise DomainError('AMEND_TARGET','补正目标报告不存在')
    try:highest=max(matches,key=lambda r:Decimal(str(r[idx['version']])))
    except Exception:raise DomainError('AMEND_TARGET','目标报告版本号无效')
    new_row=list(highest)
    for name,value in fields.items():new_row[idx[name]]=value
    new_row[idx['version']]=str(Decimal(str(highest[idx['version']]))+1)
    out=io.StringIO(newline='');writer=csv.writer(out,lineterminator='\n')
    writer.writerow(header);writer.writerows(rows);writer.writerow(new_row)
    return ('﻿'+out.getvalue()).encode('utf-8')

def _target_quarantined(state,target_id):
    return next((q['code'] for q in state['quarantine'] if q.get('id')==target_id),'')

def _summary(state):
    return {'run_id':state['run_id'],'business':state.get('business'),
            'grades':state.get('reconcile',{}).get('grades',[]),
            'seal_ready':state.get('archive',{}).get('ready',False),
            'balanced':state.get('reconcile',{}).get('balanced',False),
            'quarantine':state.get('quarantine',[])}

def adjudicate(run_id,amendment_id,target_id,fields):
    """对一份补正作出裁决，返回台账记录与新旧两版对照。"""
    if not isinstance(amendment_id,str) or not amendment_id.strip():
        raise DomainError('AMEND_ID','补正编号缺失')
    if not isinstance(fields,dict) or not fields:
        raise DomainError('AMEND_FIELDS','补正字段必须为非空对象')
    bad=sorted(set(fields)-AMENDABLE)
    if bad:raise DomainError('AMEND_FIELDS','不接受补正的字段：'+','.join(bad))
    if any(not isinstance(v,str) or not v.strip() for v in fields.values()):
        raise DomainError('AMEND_FIELDS','补正值必须为非空文本')
    body,content,params=_base(run_id)
    before=run(content,params)
    if target_id not in {r['id'] for r in before['rows']}:
        raise DomainError('AMEND_TARGET','补正目标不在当前有效报告中')
    fingerprint=_fingerprint(amendment_id,target_id,fields)
    # 重跑同一补正：编号相同且载荷一致则回放原裁决，不新增版本；载荷冲突拒绝登记。
    existing=store.find_amendment(run_id,amendment_id)
    if existing:
        if existing['payload_sha256']!=fingerprint:
            raise DomainError('AMEND_CONFLICT','同一补正编号提交了不同内容，请核对',409)
        return _ledger_view(run_id,note='同一补正已登记，本次重放未新增版本')
    new_content=_patch_content(content,target_id,fields)
    after=run(new_content,params)
    reject_code=_target_quarantined(after,target_id)
    applied=not reject_code
    chain=[a['amendment_id'] for a in store.list_amendments(run_id) if a['applied']]
    if applied:chain=chain+[amendment_id]
    entry={'base_run_id':run_id,'amendment_id':amendment_id,'target_id':target_id,
           'fields':fields,'payload_sha256':fingerprint,'out_run_id':after['run_id'],
           'applied':applied,'reject_code':reject_code,'lineage':chain}
    # 裁决快照在事务内嵌入完整补正台账，归档证据可直接呈现补正链。
    store.commit_amendment(entry,after)
    return _ledger_view(run_id,before=_summary(before),after=_summary(after),
                        note=('补正通过全链路校验，已形成当前版本' if applied
                              else '补正触发%s被整份驳回，当前依据保留旧版'%reject_code))

def _ledger_view(run_id,before=None,after=None,note=''):
    ledger=store.list_amendments(run_id)
    current_id=next((a['out_run_id'] for a in reversed(ledger) if a['applied']),run_id)
    current=store.get(current_id)
    return {'note':note,'base_run_id':run_id,'ledger':ledger,
            'current_run_id':current_id,'current_summary':_summary(current),
            'before':before,'after':after}

def ledger(run_id):
    store.get(run_id)
    return _ledger_view(run_id)

def recheck(run_id):
    """按台账到达顺序重新核对整条补正链：从原始快照逐份重放补丁并重算运行指纹。"""
    _,_,params=_base(run_id)
    base_body=store.get(run_id)
    current=base64.b64decode(base_body['source_b64'])
    records=store.list_amendments(run_id);checks=[]
    for rec in records:
        fields=json.loads(rec['fields'])
        if not rec['applied']:
            # 驳回件不改写当前版本；仅核对留痕的驳回原因在重算中仍然成立。
            trial=run(_patch_content(current,rec['target_id'],fields),params)
            still_rejected=_target_quarantined(trial,rec['target_id'])==rec['reject_code']
            checks.append({'amendment_id':rec['amendment_id'],'applied':False,
                           'reject_code':rec['reject_code'],'match':bool(still_rejected)});continue
        replayed=_patch_content(current,rec['target_id'],fields)
        state=run(replayed,params)
        match=state['run_id']==rec['out_run_id']
        checks.append({'amendment_id':rec['amendment_id'],'applied':True,
                       'stored_run_id':rec['out_run_id'],'recomputed_run_id':state['run_id'],
                       'match':match})
        if match:current=replayed
    current_state=run(current,params)
    latest_id=next((a['out_run_id'] for a in reversed(records) if a['applied']),run_id)
    return {'base_run_id':run_id,'checks':checks,
            'current_run_id':latest_id,'recomputed_current_run_id':current_state['run_id'],
            'all_match':all(c['match'] for c in checks) and current_state['run_id']==latest_id,
            'current_summary':_summary(current_state)}
