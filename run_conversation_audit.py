"""1000개 다회차 대화 검증. 법령 의미 정답 판정은 별도 원문 검토가 필요하다."""
import argparse
import concurrent.futures
import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
import zipfile

TOPICS = [
 ('기계실 조도','기계실 바닥이 100 lx인데 적합해?'),
 ('기계실 출입문','문 폭 700 mm, 높이 1800 mm야. 적합해?'),
 ('기계실 작업공간','제어반 앞 공간이 깊이 0.7 m, 폭 0.5 m야.'),
 ('기계실 바닥 단차','바닥 단차가 450 mm인데 사다리가 필요해?'),
 ('기계실 개구부','로프 통과 구멍 턱이 40 mm인데 괜찮아?'),
 ('기계실 환기','환기창이 바닥 면적의 1/20이면 충분해?'),
 ('기계실 출입 사다리','수직 사다리 높이가 3.8 m야.'),
 ('기계실 안내표지','입구에 관계자 외 출입금지 표지가 없어.'),
 ('승강장 조도','바닥 조도가 50 lx인데 괜찮아?'),
 ('승강장 출입문 높이','문 높이가 1.8 m야. 주택용이야.'),
 ('승강장 출입문 폭','장애인용인데 문 폭이 0.8 m야.'),
 ('승강장 문턱 간격','카 문턱과 승강장 문턱 간격이 40 mm야.'),
 ('카 상부 난간','벽까지 550 mm이고 난간 높이가 700 mm야.'),
 ('카 상부 발보호판','발보호판 높이가 100 mm인데 어디에 설치해?'),
 ('상부틈새','꼭대기 틈새 400 mm인데 측정 위치가 어디야?'),
 ('피트 사다리','피트 깊이 1.6 m인데 사다리가 없어.'),
 ('피트 정지스위치','정지스위치가 피트 바닥에서 1 m에 있는데 괜찮아?'),
 ('피트 하부틈새','피트 깊이가 1.5 m이면 하부틈새도 적합해?'),
 ('카 비상조명','정전 때 1 lx이고 한 시간 켜져 있어.'),
 ('카 비상통화장치','관리실 연결은 되는데 외부 통화가 안 돼.'),
 ('ARD','ARD 작동할 때 부저가 안 나와. 고장이야?'),
 ('피트 정지거리','피트 정지거리가 바닥에서 1미터에 있는데 괜찮은거야?'),
 ('피난공간','피트 피난공간이 500×700×1000 mm야.'),
 ('완충기','정격속도 1 m/s이고 유압 완충기야. 행정은 얼마나 필요해?'),
 ('승강로','두 카 사이 칸막이가 피트 바닥에서 2 m까지 있어.'),
]
DATES = ['1992-07-01','1997-08-17','1997-08-18','2000-06-15',
         '2009-11-23','2009-11-24','2013-09-14','2013-09-15',
         '2018-04-10','2020-11-20','2022-03-01','2022-03-02',
         '2025-03-01','2025-03-02','2003-06-18','2005-06-01']

def korean_date(value):
    d=datetime.date.fromisoformat(value)
    return f'{d.year}년 {d.month}월 {d.day}일'

def fatal_api_error(code):
    value=str(code or '').removeprefix('api_').lower()
    return any(w in value for w in ('credit_balance','insufficient_quota','rate_limit','invalid_api_key','billing_hard_limit')) or value in ('401','403','404','429')

def cases():
    rows=[]
    for ti,(topic,detail) in enumerate(TOPICS):
        for variant in range(40):
            date=DATES[(variant+ti)%len(DATES)];year,month,day=map(int,date.split('-'))
            mode=variant%10
            full=korean_date(date)
            if mode==0: initial=f'{year}년 {topic} 기준'
            elif mode==1: initial=f'{year}년 {month}월 허가에 {detail} {topic} 기준으로 봐줘.'
            elif mode==2: initial=f'{full} 건축허가인데 {detail} 대상은 {topic}이야.'
            elif mode==3: initial=f'설치검사 합격일이 {full}인데 {detail} {topic} 확인해줘.'
            elif mode==4: initial=f'{full} 허가 신청했고 다음 해 설치했어. {topic}, {detail}'
            elif mode==5: initial=f'{str(year)[2:]}년도 허가 {topic}. {detail}'
            elif mode==6: initial=f'허가일은 {date}, 승객용 권상식이야. {topic}: {detail}'
            elif mode==7: initial=f'{year}년 교체했는데 {topic} {detail} 원래 허가일은 몰라.'
            elif mode==8: initial=f'{topic} 기준 알려줘. {detail}'
            else: initial=f'건축허가 {full}, 설치검사는 다음 해인데 {topic}에서 {detail} 예외도 있어?'
            initial += f' 정격하중은 {450+50*variant} kg이야.'
            rows.append({'id':f'C{len(rows)+1:04d}','topic':topic,'variant':variant,'mode':mode,
                         'permit_date':date,'inspection_date':date if mode==3 else (datetime.date.fromisoformat(date)+datetime.timedelta(days=220)).isoformat(),
                         'initial':initial,'condition':detail,
                         'route':'inspection_fallback' if mode in (3,7) or (mode==0 and variant>=20) else 'permit',
                         'semantic_status':'NOT_REVIEWED',
                         'checks':['사건 날짜 구분','관련 조항과 수치 원문 대조','측정 위치와 단위','예외·적용 조건','자료 미확보와 의무 없음 구분']})
    # 사용자가 제시한 원문형 예시를 포함한다.
    rows[21*40+1]['initial']='2020년 11월 허가에 피트 정지거리가 바닥에서 1미터에 있는데 괜찮은거야?'
    rows[21*40+1]['permit_date']='2020-11-20'
    rows[20*40+3]['initial']='완성검사 합격일이 2018년 4월 10일인데 ARD 작동할때 부저가 안나와'
    rows[20*40+3]['inspection_date']='2018-04-10'
    assert len(rows)==1000 and len({r['initial'] for r in rows})==1000
    return rows

def reply_text(reply):
    return '\n'.join(v.get('simpleText',{}).get('text','') or
                     '\n'.join(v.get('basicCard',{}).get(k,'') for k in ('title','description'))
                     for v in (reply or {}).get('template',{}).get('outputs',[]))

def load_module(appdir,corpusdir):
    spec=importlib.util.spec_from_file_location('conversation_bridge',appdir/'assistant_bridge.py')
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    module.BASE=corpusdir
    return module

def run_case(module,corpus,case,live=False):
    b=module.Bridge({});b.corpus=corpus;user=case['id'];turns=[];drafts=[];api_calls=[]
    real_api=b.api_json
    def tracked(stage,payload,schema,deadline,images=None):
        api_calls.append(stage)
        value=real_api(stage,payload,schema,deadline,images)
        if stage=='answer':
            drafts.append({'question':payload.get('question'),'evidence':payload.get('evidence'),
                           'coverage':payload.get('coverage'),'draft':value})
        return value
    b.api_json=tracked
    # 오프라인 모드는 API를 모사한다. 의미 검증이나 실제 생성 성공으로 집계하지 않는다.
    if not live:
        def fake(question,previous):
            text,action=module.parse_question(question,previous)
            if module.needs_permit_date(text,action): return module.date_clarification(text)
            return {'kind':'clarify','topic':case['topic'],'cards':[{'title':'오프라인 대화 흐름 검사','topic':case['topic'],
                    'body':'실제 법령 답변은 생성하지 않았습니다.','refs':[]}]}
        b.answer=fake
    def send(message,role='followup'):
        reply,rewrite=b.handle({'userRequest':{'user':{'id':user},'utterance':message}})
        result={'input':message,'role':role,'immediate_reply':reply,'legacy_dispatch':reply is None,
                'legacy_utterance':rewrite,'legal_answer_generated':False}
        # 서버의 지연 결과 버튼과 동일한 poll 경로. 무의미한 반복 조회 대신 future 완료 대기.
        found=[(jid,j) for jid,j in b.jobs.items() if j.get('question') and not j.get('_audited')]
        if found:
            jid,job=found[-1];job['_audited']=True
            job['future'].result(timeout=210)
            result['reply']=b.poll(jid,user)
            result['model_kind']=job['future'].result().get('kind')
            result['legal_answer_generated']=result['model_kind']=='answer'
        else: result['reply']=reply
        result['text']=reply_text(result['reply'])
        result['waiting_stage']=b.sessions.get(user,{}).get('date_waiting',{}).get('stage')
        result['stored_dates']={k:b.sessions.get(user,{}).get(k) for k in ('permit_date','inspection_pass_date')}
        turns.append(result)
        return result
    started=time.monotonic()
    try:
        first=send(case['initial'],'initial')
        if first['waiting_stage']:
            if case['route']=='inspection_fallback':
                send('건축허가일은 몰라','unknown_permit')
                send('0005555 호기정보','legacy_lookup_dispatch_only')
                send('설치검사 합격날짜는 '+korean_date(case['inspection_date']),'inspection_date')
            else:
                if case['mode']==1:
                    send('허가일이 '+case['permit_date'][:7]+'까지밖에 안 나와','incomplete_date')
                send('건축허가일은 '+korean_date(case['permit_date']),'permit_date')
        # 추가 질문은 대상·조건을 다시 명시하여 이전 주제 복원과 별개로 법령 답변도 검사한다.
        date_hint=('건축허가일 '+korean_date(case['permit_date']) if case['route']=='permit' else
                   '설치검사 합격날짜 '+korean_date(case['inspection_date'])+'이고 허가일은 몰라')
        follow=send(f'{date_hint}. {case["topic"]}에 대해 {case["condition"]} 측정 위치와 예외를 구분해 줘.','conditions')
        if follow['waiting_stage']:
            if case['route']=='inspection_fallback':
                send('허가일 모름','unknown_permit_followup')
                send('설치검사 합격날짜는 '+korean_date(case['inspection_date']),'inspection_date_followup')
            else:
                send('건축허가일은 '+korean_date(case['permit_date']),'permit_date_followup')
        # 상태 연속성은 짧은 후속 질문으로도 확인한다. 새로운 조건 없는 응답 미완료는 별도 집계.
        send('근거는?','short_followup')
        return {'id':case['id'],'topic':case['topic'],'initial':case['initial'],'technical_status':'completed',
                'mode':'live' if live else 'offline_mock','turns':turns,'actual_evidence':drafts,
                'api_calls':len(api_calls),'legal_answer_turns':sum(t['legal_answer_generated'] for t in turns),
                'semantic_status':'NOT_REVIEWED',
                'flow_checks':{'answer_validation_failed_turns':sum('답변을 완성하지 못했습니다' in t['text'] for t in turns),
                               'date_waiting_at_end':bool(b.sessions.get(user,{}).get('date_waiting')),
                               'inspection_promoted_to_permit':case['route']=='inspection_fallback' and bool(b.sessions.get(user,{}).get('permit_date')),
                               'legacy_dispatch_only':any(t['legacy_dispatch'] for t in turns)},
                'seconds':round(time.monotonic()-started,2)}
    except Exception as exc:
        return {'id':case['id'],'topic':case['topic'],'initial':case['initial'],'technical_status':'failed',
                'mode':'live' if live else 'offline_mock','turns':turns,'actual_evidence':drafts,
                'api_calls':len(api_calls),'error_type':type(exc).__name__,'error_code':getattr(exc,'code','unknown'),
                'semantic_status':'NOT_REVIEWED','seconds':round(time.monotonic()-started,2)}
    finally: b.pool.shutdown(wait=True)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--app-dir',default='.')
    parser.add_argument('--corpus-dir');parser.add_argument('--output',default='conversation-results')
    parser.add_argument('--limit',type=int,default=1000);parser.add_argument('--workers',type=int,default=10,choices=range(1,11))
    parser.add_argument('--cases-file',default='audit_scenarios_next.json')
    parser.add_argument('--live',action='store_true');opt=parser.parse_args()
    if not 1<=opt.limit<=1000: parser.error('--limit must be 1..1000')
    if opt.live and not os.getenv('OPENAI_API_KEY'): parser.error('OPENAI_API_KEY is unavailable; use GitHub secrets. Do not paste it in chat.')
    if not opt.live: os.environ['OPENAI_API_KEY']='offline-placeholder-never-sent'
    app=Path(opt.app_dir).resolve();out=Path(opt.output).resolve();out.mkdir(parents=True,exist_ok=True)
    code_hash=hashlib.sha256((app/'assistant_bridge.py').read_bytes()).hexdigest()
    casefile=Path(opt.cases_file)
    if not casefile.exists(): raise SystemExit('Upload audit_scenarios_next.json next to app.py before running.')
    tests=json.loads(casefile.read_text())[:opt.limit]
    if len({c['id'] for c in tests})!=len(tests): raise SystemExit('Duplicate case IDs')
    case_hash=hashlib.sha256(json.dumps(tests,ensure_ascii=False).encode()).hexdigest()
    module=load_module(app,Path(opt.corpus_dir).resolve() if opt.corpus_dir else app);corpus=module.Corpus()
    summaryfile=out/'summary.json';answersfile=out/'conversation_answers.jsonl'
    if answersfile.exists():
        prior=json.loads(summaryfile.read_text()) if summaryfile.exists() else {}
        if (prior.get('code_sha256'),prior.get('case_sha256'),prior.get('live_mode'))!=(code_hash,case_hash,opt.live):
            raise SystemExit('Output has different code/cases/mode. Choose a new output folder.')
    (out/'scenarios.json').write_text(json.dumps(tests,ensure_ascii=False,indent=2))
    results=[json.loads(l) for l in answersfile.read_text().splitlines()] if answersfile.exists() else []
    # 과거 실패를 성공처럼 건너뛰지 않는다. 재실행 결과는 같은 ID의 과거 실패와 교체한다.
    results=list({r['id']:r for r in results}.values())
    completed={r['id'] for r in results if r['technical_status']=='completed'};started=time.monotonic()
    def save_summary():
        summary={'scenarios_requested':len(tests),'scenarios_recorded':len(results),'completed':sum(r['technical_status']=='completed' for r in results),
                 'failed':sum(r['technical_status']=='failed' for r in results),'turns_recorded':sum(len(r['turns']) for r in results),
                 'legal_answer_turns':sum(sum(t.get('model_kind')=='answer' for t in r['turns']) for r in results) if opt.live else 0,
                 'scenarios_with_legal_answers':sum(any(t.get('model_kind')=='answer' for t in r['turns']) for r in results) if opt.live else 0,
                 'answer_validation_failed_turns':sum(r.get('flow_checks',{}).get('answer_validation_failed_turns',0) for r in results),
                 'date_waiting_at_end':sum(r.get('flow_checks',{}).get('date_waiting_at_end',False) for r in results),
                 'inspection_promoted_to_permit':sum(r.get('flow_checks',{}).get('inspection_promoted_to_permit',False) for r in results),
                 'semantic_verified':0,'live_mode':opt.live,'workers':opt.workers,'code_sha256':code_hash,'case_sha256':case_hash,
                 'seconds_this_run':round(time.monotonic()-started,2),
                 'limits':['completed means conversation processing only, not correct law.',
                           'Legacy lookup is dispatch-only: dummy ID does not call public lookup APIs.',
                           'Kakao screen rendering and actual channel routes are not tested.',
                           'Source-by-source semantic review remains NOT_REVIEWED.']}
        summaryfile.write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    save_summary()
    pending=[c for c in tests if c['id'] not in completed]
    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=opt.workers) as pool:
            for start in range(0,len(pending),opt.workers):
                batch=list(pool.map(lambda c:run_case(module,corpus,c,opt.live),pending[start:start+opt.workers]))
                replacement={r['id']:r for r in results}
                replacement.update({r['id']:r for r in batch})
                results=list(replacement.values())
                temporary=answersfile.with_suffix('.tmp')
                with temporary.open('w') as f:
                    for row in results: f.write(json.dumps(row,ensure_ascii=False)+'\n')
                temporary.replace(answersfile)
                save_summary();print(f'{len(results)}/{len(tests)} scenarios recorded',flush=True)
                if any(fatal_api_error(r.get('error_code')) for r in batch):
                    print('API billing/auth/rate limit failure: stop before the next batch.',flush=True);break
    finally:
        save_summary()
        with zipfile.ZipFile(out.parent/(out.name+'.zip'),'w',zipfile.ZIP_DEFLATED) as z:
            for p in out.iterdir():
                if p.is_file():z.write(p,p.name)
    print(json.dumps(json.loads(summaryfile.read_text()),ensure_ascii=False,indent=2))
if __name__=='__main__': main()
