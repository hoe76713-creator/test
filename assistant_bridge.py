            citations = [source_label(self.corpus.sources[ref.split(':')[0]])+' '+ref.split(':')[1]+'쪽'
                         for ref in refs]
            section = '■ '+item['title']+'\n'+body
            if citations:
                section += '\n근거: '+' / '.join(citations)
            sections.append(section)
        if limited:
            sections.append('※ 확인된 범위의 이력입니다. 관련 원문 전체의 검토가 완료된 것은 아닙니다.')
        return {'kind': result.get('kind', 'clarify'), 'topic': result.get('topic', ''),
                'history_text': '\n\n'.join(sections)}

    def answer(self, question, previous_topic):
        started = time.monotonic()
        deadline = started + 170
        task = uuid.uuid4().hex[:8]
        try:
            with self.corpus_lock:
                if self.corpus is None:
                    load = time.monotonic()
                    self.corpus = Corpus()
                    LOG.info('task=%s stage=corpus_load seconds=%.2f pages=%d',
                             task, time.monotonic()-load, len(self.corpus.pages))
            text, action = parse_question(question, previous_topic)
            date_context = historical_context(text)
            broad = broad_topic(text)
            layout = None  # 큰 주제는 handle의 원문 목차 안내에서 처리한다.
            if broad:
                plan = {'kind': 'answer', 'topic': broad,
                        'terms': [x for row in LAYOUTS[broad] for x in row[2]], 'message': ''}
            elif (section := re.search(r'(?<![\d.])(\d+(?:\.\d+){1,5})(?![\d.])', text)):
                plan = {'kind': 'answer', 'topic': text,
                        'terms': [section.group(1)], 'message': ''}
            else:
                plan = self.api_json('plan', {'question': text, 'action': action,
                                              'previous_topic': previous_topic}, PLAN_SCHEMA, deadline)
            if plan['kind'] != 'answer':
                if plan['kind'] in ('selfcheck', 'insurance', 'runby'):
                    return plan
                return {'kind': plan['kind'], 'topic': plan['topic'],
                        'cards': [{'title': '질문 확인', 'topic': plan['topic'],
                                   'body': plan['message'][:250], 'refs': []}]}
            retrieval = time.monotonic()
            retrieval_terms = plan['terms']+[plan['topic']]
            if date_context['years']:
                retrieval_terms.extend([str(y)+'년' for y in date_context['years']])
                if '기계실' in compact(text) and any(t in text for t in ('조도', '조명')):
                    retrieval_terms.append('기계실조도')
            evidence, coverage = self.collect(retrieval_terms, action, layout)
            LOG.info('task=%s stage=retrieve seconds=%.2f pages=%d omitted=%d', task,
                     time.monotonic()-retrieval, len(evidence), len(coverage['omitted_refs']))
            if not evidence:
                return {'kind': 'clarify', 'topic': plan['topic'], 'cards': [{
                    'title': '근거 확인', 'topic': plan['topic'], 'refs': [],
                    'body': '이번 검색에서 관련 근거를 확인하지 못했습니다. 대상과 설치 조건을 구체적으로 알려주세요.'}]}
            c = self.corpus
            known = {p['ref'] for p in evidence}
            visual_seen = set()
            images = []
            limited = bool(coverage['omitted_refs']) or (action in ('history', 'application') and any(x['not_selected'] for x in coverage['sources']))
            last = None
            for attempt in range(3):
                # 검색·추가 열람은 최대 한 번만 일괄 수행하고 반드시 최종 작성 단계로 진행한다.
                final_pass = attempt >= 1
                payload = {'question': text, 'action': action, 'topic': plan['topic'],
                           'layout': [{'title': r[0], 'topic': r[1]} for r in layout] if layout else [],
                           'latest_source': c.latest, 'evidence': evidence, 'coverage': coverage,
                           'historical_context': date_context,
                           'visual_evidence': sorted(visual_seen), 'final_pass': final_pass,
                           'validation_feedback': last}
                result = self.api_json('answer', payload, ANSWER_SCHEMA, deadline, images)
                requested = result.get('read_refs', [])+result.get('visual_refs', [])
                extra_terms = result.get('search_terms', [])
                if attempt == 0 and (requested or extra_terms):
                    extra_start = time.monotonic()
                    if extra_terms:
                        extra, extra_coverage = self.collect(extra_terms, action, None)
                        for page in extra:
                            if page['ref'] not in known and len(evidence) < 70:
                                evidence.append(page)
                                known.add(page['ref'])
                        coverage['additional_search'] = extra_coverage
                    requested = list(dict.fromkeys(requested))
                    valid = [ref for ref in requested if ref in c.lookup]
                    added, _ = c.read(valid[:16])
                    for page in added:
                        if page['ref'] not in known:
                            evidence.append(page)
                            known.add(page['ref'])
                    visuals = [ref for ref in dict.fromkeys(result.get('visual_refs', [])) if ref in known][:8]
                    if visuals:
                        _, pictures = c.read(visuals, True)
                        images.extend(pictures)
                        visual_seen.update(visuals)
                    omitted = set(requested)-known
                    omitted_visuals = set(result.get('visual_refs', []))-visual_seen
                    limited = limited or bool(omitted or omitted_visuals)
                    coverage['unread_requested_refs'] = sorted(omitted)
                    coverage['unread_requested_visuals'] = sorted(omitted_visuals)
                    LOG.info('task=%s stage=additional_read seconds=%.2f pages=%d images=%d',
                             task, time.monotonic()-extra_start, len(added), len(visuals))
                    continue
                if final_pass and (requested or extra_terms):
                    limited = True
                try:
                    if action == 'history':
                        return self.format_history(result, known, limited)
                    return self.format_result(result, known, layout, limited)
                except (ValueError, KeyError, TypeError) as exc:
                    last = str(exc)
                    LOG.warning('task=%s stage=validate attempt=%d code=%s', task, attempt+1, last)
            # 횟수 한도를 자료 없음이나 전수검토 완료로 바꾸지 않는다.
            return {'kind': 'clarify', 'topic': plan['topic'], 'cards': [{
                'title': '확인 범위 안내', 'topic': plan['topic'], 'refs': [],
                'body': '관련 원문은 찾았지만 근거와 출력 형식을 검증한 답변을 완성하지 못했습니다. 대상이나 확인할 시점을 좁혀 주세요.'}]}
        finally:
            LOG.info('task=%s stage=total seconds=%.2f', task, time.monotonic()-started)
