import sqlite3,json,os
from datetime import datetime,timezone
from pathlib import Path
from app.errors import DomainError

def connection():
    path=Path(os.environ.get('DB_PATH','data/records.db'));path.parent.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(path,timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, decision TEXT NOT NULL DEFAULT "待复核")')
    db.execute('CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY,value TEXT NOT NULL)')
    # 补正台账为只追加表：原始报告与每份补正均不可删除、不可覆盖。
    db.execute('CREATE TABLE IF NOT EXISTS amendments ('
        'base_run_id TEXT NOT NULL, amendment_id TEXT NOT NULL, seq INTEGER NOT NULL, '
        'received_at TEXT NOT NULL, target_id TEXT NOT NULL, fields TEXT NOT NULL, '
        'payload_sha256 TEXT NOT NULL, out_run_id TEXT NOT NULL, applied INTEGER NOT NULL, '
        'reject_code TEXT NOT NULL DEFAULT "", lineage TEXT NOT NULL, '
        'PRIMARY KEY(base_run_id,amendment_id))')
    return db

def save(run_id, body):
    with connection() as db:
        db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',(run_id,json.dumps(body,ensure_ascii=False)))
        db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(run_id,))
    return get(run_id)

def get(run_id):
    with connection() as db:r=db.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
    if not r:raise DomainError('NOT_FOUND','批次不存在',404)
    body=json.loads(r['body']);body.update(run_id=r['id'],review_version=r['version'],decision=r['decision']);return body

def history():
    with connection() as db:rows=db.execute("SELECT id,version,decision FROM runs ORDER BY CASE WHEN id=(SELECT value FROM meta WHERE key='latest') THEN 0 ELSE 1 END,rowid DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

def run_ids():
    with connection() as db:return [r['id'] for r in db.execute('SELECT id FROM runs ORDER BY rowid').fetchall()]

def review(run_id, version, decision):
    if decision not in ('通过','退回'):raise DomainError('DECISION','复核状态无效')
    with connection() as db:
        cur=db.execute('UPDATE runs SET version=version+1,decision=? WHERE id=? AND version=?',(decision,run_id,version))
        if cur.rowcount!=1:
            if not db.execute('SELECT 1 FROM runs WHERE id=?',(run_id,)).fetchone():raise DomainError('NOT_FOUND','批次不存在',404)
            raise DomainError('STALE','复核版本已更新，请刷新',409)
    return get(run_id)

def find_amendment(base_run_id, amendment_id):
    with connection() as db:
        r=db.execute('SELECT * FROM amendments WHERE base_run_id=? AND amendment_id=?',(base_run_id,amendment_id)).fetchone()
    return dict(r) if r else None

def list_amendments(base_run_id):
    with connection() as db:
        rows=db.execute('SELECT * FROM amendments WHERE base_run_id=? ORDER BY seq',(base_run_id,)).fetchall()
    return [dict(r) for r in rows]

def commit_amendment(entry, run_body):
    """单事务提交一次补正裁决。

    裁决结果（新运行快照与台账行）同时落库：事务失败时二者皆不生效，
    上一当前版本保持完好（写入中断保留旧版）。被驳回的补正同样留痕，
    但不移动 latest 指针，当前依据仍是旧版。
    """
    with connection() as db:
        dup=db.execute('SELECT 1 FROM amendments WHERE base_run_id=? AND amendment_id=?',
                       (entry['base_run_id'],entry['amendment_id'])).fetchone()
        if dup:raise DomainError('AMEND_DUP','补正已登记，禁止重复裁决')
        seq=db.execute('SELECT COALESCE(MAX(seq),0)+1 AS n FROM amendments WHERE base_run_id=?',
                       (entry['base_run_id'],)).fetchone()['n']
        record=dict(entry);record['seq']=seq
        if not record.get('received_at'):
            record['received_at']=datetime.now(timezone.utc).isoformat()
        db.execute('INSERT INTO amendments(base_run_id,amendment_id,seq,received_at,target_id,fields,'
                   'payload_sha256,out_run_id,applied,reject_code,lineage) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (record['base_run_id'],record['amendment_id'],seq,record['received_at'],
                    record['target_id'],json.dumps(record['fields'],ensure_ascii=False),
                    record['payload_sha256'],record['out_run_id'],1 if record['applied'] else 0,
                    record.get('reject_code',''),json.dumps(record['lineage'],ensure_ascii=False)))
        # 运行快照内嵌截至本次裁决的完整补正台账（含驳回件），封存证据不丢任何记录。
        run_body=dict(run_body)
        run_body['amendments']=[dict(r) for r in db.execute(
            'SELECT * FROM amendments WHERE base_run_id=? ORDER BY seq',(record['base_run_id'],)).fetchall()]
        db.execute('INSERT OR IGNORE INTO runs(id,body) VALUES (?,?)',
                   (record['out_run_id'],json.dumps(run_body,ensure_ascii=False)))
        if record['applied']:
            db.execute("INSERT INTO meta(key,value) VALUES ('latest',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       (record['out_run_id'],))
    return record
