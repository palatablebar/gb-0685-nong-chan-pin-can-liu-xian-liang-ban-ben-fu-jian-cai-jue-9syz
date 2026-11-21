import json
from pathlib import Path
from flask import Blueprint,request,jsonify,render_template
from app.config import TITLE,MODULES,EXTRA_MODULES,SHORT
from app.errors import DomainError
from app.modules.m8_amend import service
from app import store

ALL_MODULES=MODULES+EXTRA_MODULES
bp=Blueprint('m8_amend',__name__)

def _body():
    data=request.get_json(silent=True)
    if not isinstance(data,dict):raise DomainError('PAYLOAD','请求必须是对象')
    run_id=data.get('run_id')
    if not isinstance(run_id,str) or not run_id.strip():raise DomainError('AMEND_BASE','缺少运行批次号')
    return data,run_id.strip()

@bp.get('/m8_amend')
def screen():
    latest=store.history()
    run_id=latest[0]['id'] if latest else None
    if run_id:
        # latest 可能是补正输出快照：从其内嵌台账反查所属原始批次
        snapshot=store.get(run_id)
        embedded=snapshot.get('amendments') or []
        if embedded:run_id=embedded[0]['base_run_id']
    view=service.ledger(run_id) if run_id else {'base_run_id':'','ledger':[],'current_run_id':'','current_summary':{}}
    demo=json.loads((Path(__file__).resolve().parents[3]/'sample_data'/'amendments_demo.json').read_text(encoding='utf-8'))
    return render_template('m8_amend.html',title=TITLE,short=SHORT,modules=ALL_MODULES,current=len(ALL_MODULES),
                           data=view,run={'decision':'补正裁决','review_version':'台账'},history=latest,demo=demo)

@bp.get('/api/m8_amend')
def ledger_view():
    run_id=request.args.get('run_id')
    if not run_id:raise DomainError('AMEND_BASE','缺少运行批次号')
    return jsonify(service.ledger(run_id))

@bp.post('/api/m8_amend')
def submit():
    data,run_id=_body()
    return jsonify(service.adjudicate(run_id,str(data.get('amendment_id','')).strip(),
                                      str(data.get('target_id','')).strip(),data.get('fields')))

@bp.post('/api/m8_amend/recheck')
def recheck():
    data,run_id=_body()
    return jsonify(service.recheck(run_id))
