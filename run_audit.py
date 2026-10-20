"""승강기 질문 10000건 검사. --live는 실제 API 호출이며 기존 환경변수 사용.
오프라인 검사는 검색 준비 단계만 검사한다. 의미 일치 판정을 대신하지 않는다.
python run_audit.py --app-dir . --output audit-results
python run_audit.py --app-dir . --output audit-results --live --limit 10000
"""
import argparse
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

TOPICS = [
 ('기계실 조도', ['기계실조도','3.1.5(6)','6.3.7','6.1.4'], 'source-02:12'),
 ('기계실 출입문 크기', ['기계실','출입문','6.3.4','6.3.2'], None),
 ('기계실 작업공간', ['기계실','작업구역','6.3.3','6.6.3.2'], None),
 ('기계실 바닥 단차', ['기계실','단차','높이차','6.6.3.2.4'], None),
 ('기계실 개구부', ['기계실','기타개구부','6.3.5','6.6.3.3'], None),
 ('기계실 환기', ['기계실','환기','6.3.6','6.1.3'], None),
 ('기계실 출입 사다리', ['기계실사다리','6.2.5'], None),
 ('기계실 안내표지', ['기계실','안내표지','6.6.2'], None),
 ('승강장 조도', ['승강장조명','승강장','7.6.1','7.7.1'], None),
 ('승강장 출입문 높이', ['출입구','높이','7.2.1'], None),
 ('승강장 출입문 폭', ['출입구','폭','7.2.2'], None),
 ('승강장 문턱 간격', ['문턱','수평','7.4.1'], None),
 ('카 상부 난간 높이', ['난간','4.1.3(20)','8.13.3','8.7.4'], None),
 ('카 상부 발보호판', ['발보호판','8.13.3','8.7.2'], None),
 ('상부틈새', ['상부틈새','6.5.7','5.7.1'], None),
 ('피트 사다리', ['피트','사다리','5.7.3.2','부속서'], None),
 ('피트 정지스위치', ['피트이스탑','정지장치','정지스위치','5.7.3'], None),
 ('피트 하부틈새', ['피트','하부틈새','6.5.8','5.7.3'], None),
 ('카 비상조명', ['예비조명','비상조명','8.17.4','8.10.4'], None),
 ('카 비상통화장치', ['비상통화','경보장치','14.2.3','14.2.3.1'], None),
]
YEARS = [(str(y),y) for y in range(1992,2027)] + [(str(y)[2:],y) for y in range(1992,2007)]
EVENTS = [('허가','건축허가'),('건축허가','건축허가'),
          ('허가 신청','건축허가 신청'),('건축허가 신청','건축허가 신청'),
          ('설치','설치'),('설치 완료','설치'),
          ('설치검사','설치검사'),('최초 설치검사','설치검사'),
          ('교체','교체'),('리모델링','교체')]


def cases():
    result=[]
    for topic, terms, anchor in TOPICS:
        for short, year in YEARS:
            for label,event in EVENTS:
                result.append({'id':f'Q{len(result)+1:04d}',
                  'question':f'{short}년 {label}인데 {topic} 기준',
                  'topic':topic,'year':year,'event':event,'terms':terms,'old_anchor':anchor})
    assert len(result)==10000 and len({r['question'] for r in result})==10000
    return result


def write_csv(path, rows):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def main():
    args=argparse.ArgumentParser()
    args.add_argument('--app-dir',required=True)
    args.add_argument('--output',default='audit-results')
    args.add_argument('--corpus-dir',default=None)
    args.add_argument('--live',action='store_true')
    args.add_argument('--limit',type=int,default=10000)
    opt=args.parse_args()
    appdir=Path(opt.app_dir).resolve();out=Path(opt.output).resolve();out.mkdir(parents=True,exist_ok=True)
    spec=importlib.util.spec_from_file_location('tested_bridge',appdir/'assistant_bridge.py')
    bridge_module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=bridge_module;spec.loader.exec_module(bridge_module)
    bridge_module.BASE=Path(opt.corpus_dir).resolve() if opt.corpus_dir else appdir
    bridge=bridge_module.Bridge({});bridge.corpus=bridge_module.Corpus()
    tests=cases()[:max(0,min(opt.limit,10000))]
    (out/'questions.json').write_text(json.dumps(tests,ensure_ascii=False,indent=2),encoding='utf-8')
    write_csv(out/'questions.csv',[{k:v for k,v in r.items() if k not in ('terms','old_anchor')} for r in tests])
    has_key=bool(os.getenv('OPENAI_API_KEY'))
    if opt.live and not has_key:
        raise SystemExit('OPENAI_API_KEY is unavailable. Set it in the server environment; do not send it in chat.')
    completed=set()
    livepath=out/'live_answers.jsonl'
    if opt.live and livepath.exists():
        for line in livepath.read_text().splitlines():
            value=json.loads(line)
            if value.get('technical_status')=='generated':completed.add(value['id'])
    rows=[];cache={};started=time.monotonic()
    try:
        for i,case in enumerate(tests):
            question=case['question']
            context=bridge_module.historical_context(question)
            parsed,action=bridge_module.parse_question(question)
            terms=case['terms']+[str(case['year'])+'년']
            cachekey=(case['topic'],case['year'],action)
            if cachekey not in cache:
                evidence,coverage=bridge.collect(terms,action,None)
                cache[cachekey]=(evidence,coverage)
            evidence,coverage=cache[cachekey]
            refs={p['ref'] for p in evidence}
            bysource={p['ref'].split(':')[0] for p in evidence}
            lawids={sid for sid in bysource if bridge.corpus.sources[sid]['category']=='law'}
            old_present='source-02' in bysource
            addenda_present=any(bridge.corpus.sources[sid]['category']=='addenda' for sid in bysource)
            anchor=case['old_anchor']
            anchor_ok=(anchor in refs) if anchor else None
            checks=[context['years']==[case['year']],case['event'] in context['events'],
                    action=='application',bool(evidence),addenda_present,
                    old_present if case['year']<2013 else len(lawids)>1]
            if anchor:checks.append(bool(anchor_ok))
            guard=bridge.historical_guard(context,evidence,case['topic'])
            original_version_check=checks[5]
            if case['topic']=='카 상부 발보호판' and case['year']<=2013:
                # 今回は旧資料の存在強制ではなく初出条項・経過措置の照合を検査する。
                checks[5]=('source-03:41' in refs and 'source-01:7' in refs
                           and guard['status']=='earliest_provision_and_transition_review_required')
            if case['topic']=='상부틈새':
                checks[5]=checks[5] and {'source-02:8','source-02:9'}<=refs
            checks.append(guard['can_answer'])
            failures=[]
            for name,ok in zip(['year','event','past_routing','evidence','addenda','historical_versions'],checks[:6]):
                if not ok:failures.append(name)
            if anchor and not anchor_ok:failures.append('old_lighting_anchor')
            rows.append({'id':case['id'],'question':question,'year':case['year'],'event':case['event'],
              'preliminary_status':'PASS' if all(checks) else 'FAIL',
              'failures':','.join(failures),'evidence_pages':len(evidence),
              'old_generic_version_rule':str(original_version_check),
              'historical_guard':guard['status'],
              'source_ids':','.join(sorted(bysource)),
              'lighting_anchor_present':str(anchor_ok) if anchor else 'NOT_ASSESSED',
              'live_answer':'NOT_EXECUTED' if not opt.live else 'SEE_JSONL',
              'answer_semantic_accuracy':'NOT_REVIEWED',
              'method':'known topic terms supplied; actual planner and answer NOT exercised offline'})
            if opt.live and case['id'] not in completed:
                item={'id':case['id'],'question':question,'semantic_status':'NOT_REVIEWED'}
                t=time.monotonic()
                try:
                    # Capture the evidence actually supplied to the model, not a separate guessed ground truth.
                    traces=[];original_api=bridge.api_json
                    def tracked(stage,payload,schema,deadline,images=None):
                        if stage=='answer':
                            traces.append({'action':payload.get('action'),
                              'historical_context':payload.get('historical_context'),
                              'evidence':payload.get('evidence',[]),'coverage':payload.get('coverage',{})})
                        return original_api(stage,payload,schema,deadline,images)
                    bridge.api_json=tracked
                    item['answer']=bridge.answer(question,'')
                    item['actual_evidence']=traces
                    item['technical_status']='generated'
                    # No automatic declaration of legal accuracy, even when JSON and refs are valid.
                except Exception as exc:
                    item['technical_status']='failed';item['error_type']=type(exc).__name__
                    item['error_code']=getattr(exc,'code','unknown')
                finally:
                    bridge.api_json=original_api
                item['seconds']=round(time.monotonic()-t,2)
                with livepath.open('a',encoding='utf-8') as f:f.write(json.dumps(item,ensure_ascii=False)+'\n')
                if item['technical_status']=='failed' and (item.get('error_code','').startswith('api_')):
                    print('Stopped after API failure; results were saved.',flush=True);break
            if (i+1)%1000==0:print(f'processed {i+1}/{len(tests)}',flush=True)
    finally:
        bridge.pool.shutdown(wait=True)
        if rows:write_csv(out/'preliminary_results.csv',rows)
    summary={'requested_questions':10000,'generated_questions':len(tests),'preliminary_cases':len(rows),
       'preliminary_pass':sum(r['preliminary_status']=='PASS' for r in rows),
       'preliminary_fail':sum(r['preliminary_status']=='FAIL' for r in rows),
       'live_mode':opt.live,'api_key_available':has_key,
       'answers_semantically_verified':0,'seconds':round(time.monotonic()-started,2),
       'code_sha256':hashlib.sha256((appdir/'assistant_bridge.py').read_bytes()).hexdigest(),
       'limits':['Offline tests use supplied topic terms, not the live planner.',
                 'Retrieved pages do not prove answer correctness or legal applicability.',
                 'Only generated answers with independent original-document review can establish correctness.',
                 'Kakao rendering, delayed result buttons, live APIs and cross-user state are not tested here.']}
    (out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
