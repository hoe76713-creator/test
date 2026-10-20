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
