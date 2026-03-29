from flask import Flask, request, jsonify
import requests
import xml.etree.ElementTree as ET
import datetime
import re
import os

app = Flask(__name__)

# API 설정
KEY = 'e3cee5ce5cec33048ec4e796503f3e3e2c17cf9bacae0079b46590af0d3ca1dd'
URLS = {
    'BULD': "http://apis.data.go.kr/B553664/BuldElevatorService/getBuldElvtrList",
    'SAFE': "http://apis.data.go.kr/B553664/ElevatorSafeMngrService/getSafeMngrList",
    'INSUR': "http://apis.data.go.kr/B553664/ElevatorInsuranceService/getElvtrInsurance",
    'CHECK': "http://apis.data.go.kr/B553664/ElevatorSelfCheckService/getSelfCheckList",
    'SPEC': "http://apis.data.go.kr/B553664/ElevatorOperationService/getOperationInfoListV1",
}

# --- 헬퍼 함수 (조회 및 포맷) ---
def get_api(url, params):
    try:
        res = requests.get(url, params=params, timeout=5.0)
        return ET.fromstring(res.content.strip()) if res.status_code == 200 else None
    except: return None

def format_dt(s):
    if not s or s in ["--", ""]: return "정보없음"
    c = s.replace("-", "").strip()
    return f"{c[:4]}년 {c[4:6]}월 {c[6:]}일" if len(c) == 8 else s

def get_info(no):
    root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': no})
    if root is not None and root.find('.//item') is not None:
        it = root.find('.//item')
        return {
            "buldNm": it.findtext('buldNm') or "정보없음",
            "addr": it.findtext('address1') or "주소 정보 없음",
            "asign": it.findtext('elvtrAsignNo') or "-",
        }
    return {"buldNm": "정보없음", "addr": "정보없음", "asign": "-"}

def kakao_res(outputs):
    return jsonify({"version": "2.0", "template": {"outputs": outputs}})

# --- 리포트 생성 함수 ---
def make_spec_report(no, info):
    addr_short = " ".join(info['addr'].split()[:3])
    root = get_api(URLS['SPEC'], {'serviceKey': KEY, 'elevator_no': no, 'buld_address': addr_short})
    if root is not None and root.find('.//item') is not None:
        it = root.find('.//item')
        return (f"⚙️ [호기별 제원표]\n━━━━━━━━━━━━━━\n🏷️ 모델명: {it.findtext('elvtrModel') or '-'}\n💨 정격속도: {it.findtext('ratedSpeed') or '-'}m/s\n🏗️ 최초 설치일: {format_dt(it.findtext('installationDe'))}\n📦 하중: {it.findtext('liveLoad')}kg\n👨‍👩‍👧 정원: {it.findtext('ratedCap')}명\n↕️ 층수: {it.findtext('shuttleFloorCnt')}층")
    return "⚠️ 제원 조회 실패"

def make_check_report(no, info):
    res_list, last_co = [], "확인불가"
    today = datetime.datetime.now()
    for i in range(3):
        ym = (today - datetime.timedelta(days=i*31)).strftime("%Y%m")
        root = get_api(URLS['CHECK'], {'serviceKey': KEY, 'elevator_no': no, 'yyyymm': ym})
        if root is not None and root.find('.//item') is not None:
            it = root.find('.//item')
            last_co = (it.findtext('companyNm') or last_co).strip()
            res_list.append(f"📅 {ym[:4]}년 {ym[4:]}월 점검\n✅ 결과: {it.findtext('selchkResultNm')}\n🛠️ 업체: {last_co}")
    report = f"🛠️ [자체점검일지 내역]\n━━━━━━━━━━━━━━\n" + ("\n\n".join(res_list) if res_list else "⚠️ 점검 데이터 없음")
    return report

def make_insur_report(no, info):
    today_ym = datetime.datetime.now().strftime("%Y%m")
    root = get_api(URLS['INSUR'], {'serviceKey': KEY, 'elevator_no': no, 'cont_ymd': today_ym})
    if root is not None and root.find('.//item') is not None:
        it = root.find('.//item')
        return (f"🛡️ [보험 가입 확인]\n━━━━━━━━━━━━━━\n🏢 보험사: {it.findtext('companyNm')}\n⏳ 만료일: {format_dt(it.findtext('contEnDe'))}")
    return f"⚠️ {no} 보험 정보 없음"

@app.route('/ask', methods=['POST'])
def ask():
    try:
        content = request.get_json()
        raw_msg = content['userRequest']['utterance']
        utterance = raw_msg.strip().replace(" ", "")
        all_digits = re.findall(r'\d+', raw_msg)
        elv_no = all_digits[0][:7] if all_digits else ""

        # =========================================================
        # 4자리 숫자(설치 연도)를 입력했을 때 자동으로 주기 계산
        # =========================================================
        if utterance.isdigit() and len(utterance) == 4:
            install_year = int(utterance)
            year_15 = install_year + 15
            year_18 = install_year + 18
            year_21 = install_year + 21
            
            report = (
                f"📅 [정밀검사 대상 기간 확인]\n"
                f"🏗️ 기준 설치 연도: {install_year}년\n"
                f"━━━━━━━━━━━━━━\n"
                f"🔍 정밀검사 예정 주기\n"
                f"1️⃣ 1차 (15년 경과): {year_15}년\n"
                f"2️⃣ 2차 (18년 경과): {year_18}년\n"
                f"3️⃣ 3차 (21년 경과): {year_21}년\n\n"
                f"💡 (참고) 정확한 검사 대상일은 호기별 제원표의 상세 '설치일' 기준으로 산정됩니다."
            )
            return kakao_res([{"simpleText": {"text": report}}])

        # =========================================================
        # 자격요건 진단 (이모지 교체: 🔍 -> 🏅)
        # =========================================================
        if "자격요건" in utterance or "자격확인" in utterance:
            if "진단_" not in utterance and "결과" not in utterance:
                return kakao_res([{"basicCard": {"title": "🏅 [자격진단] 1단계", "description": "화재 시 대피용으로 지정된\n'피난용 엘리베이터'를 관리하시나요?", "buttons": [
                    {"action": "message", "label": "예 (피난용 있음)", "messageText": "자격요건_피난용_결과"},
                    {"action": "message", "label": "아니오", "messageText": "자격요건_진단_16층"}]}}])

        if "자격요건_진단_16층" in utterance:
            return kakao_res([{"basicCard": {"title": "🏅 [자격진단] 2단계", "description": "건축물의 '지상층'이 16층 이상인가요?\n(※ 지하층은 제외합니다)", "buttons": [
                {"action": "message", "label": "예 (지상 16층 이상)", "messageText": "자격요건_다중이용_결과"},
                {"action": "message", "label": "아니오", "messageText": "자격요건_진단_문화집회"}]}}])

        diag_flow = [
            ("문화집회", "문화 및 집회시설에 해당하시나요?\n(동물원 및 식물원 제외)", "종교"),
            ("종교", "종교시설에 해당하시나요?", "판매"),
            ("판매", "판매시설에 해당하시나요?", "여객"),
            ("여객", "운수시설 중 여객용 시설에 해당하시나요?", "종합병원"),
            ("종합병원", "의료시설 중 종합병원에 해당하시나요?", "관광숙박"),
            ("관광숙박", "숙박시설 중 관광숙박시설에 해당하시나요?", "일반결정")
        ]
        for current, quest, next_step in diag_flow:
            if f"자격요건_진단_{current}" in utterance:
                target_next = "자격요건_일반_결과" if next_step == "일반결정" else f"자격요건_진단_{next_step}"
                return kakao_res([{"basicCard": {"title": "🏅 용도 확인", "description": quest, "buttons": [
                    {"action": "message", "label": "예", "messageText": "자격요건_진단_면적"},
                    {"action": "message", "label": "아니오", "messageText": target_next}]}}])

        if "자격요건_진단_면적" in utterance:
            return kakao_res([{"basicCard": {"title": "📐 바닥면적 확인", "description": "해당 용도로 쓰는 바닥면적의 합계가\n5,000제곱미터 이상인가요?", "buttons": [
                {"action": "message", "label": "예 (5,000㎡ 이상)", "messageText": "자격요건_다중이용_결과"},
                {"action": "message", "label": "아니오 (5,000㎡ 미만)", "messageText": "자격요건_일반_결과"}]}}])

        # --- 자격요건 결과값 버튼 명칭 구조도 반영 ---
        if "자격요건_피난용_결과" in utterance:
            desc = ("🚨 [피난용 엘리베이터 자격 안내]\n━━━━━━━━━━━━━━\n해당 승강기는 화재 시 인명 구조용으로 사용되므로 **일반 교육만으로는 선임이 불가능**합니다.\n\n"
                    "✅ **필수 자격 요건 (중 하나)**\n1️⃣ 기능사 이상 자격증\n2️⃣ 관련학과 졸업 학위\n3️⃣ 6개월 이상의 실무 경력")
            return kakao_res([{"basicCard": {"title": "피난용 자격 진단 결과", "description": desc, "buttons": [
                {"action": "webLink", "label": "✍️ 선임 신고하러 가기", "webLinkUrl": "https://minwon.koelsa.or.kr/"},
                {"action": "webLink", "label": "❓ 선임 신청 방법 안내", "webLinkUrl": "https://youtu.be/gtMaxkUw4cc?si=00Rl407_MMy1iNZS"}]}}])

        if "자격요건_다중이용_결과" in utterance:
            desc = ("🏙️ [다중이용 건축물 자격 안내]\n━━━━━━━━━━━━━━\n16층 이상 또는 다중이용시설의 안전관리자는 **기술적인 기본 역량**이 필요합니다.\n\n"
                    "✅ **선임 가능 조건**\n👉 자격증/학위/경력 보유자\n👉 또는 **'행안부 기술 기본교육'** 이수 시 선임 가능합니다.")
            return kakao_res([{"basicCard": {"title": "다중이용 자격 진단 결과", "description": desc, "buttons": [
                {"action": "webLink", "label": "✍️ 선임 신고하러 가기", "webLinkUrl": "https://minwon.koelsa.or.kr/"},
                {"action": "webLink", "label": "❓ 선임 신청 방법 안내", "webLinkUrl": "https://youtu.be/gtMaxkUw4cc?si=00Rl407_MMy1iNZS"}]}}])

        if "자격요건_일반_결과" in utterance:
            desc = ("🏠 [일반 건축물 자격 안내]\n━━━━━━━━━━━━━━\n해당 건물은 가장 보편적인 자격 요건이 적용됩니다.\n\n"
                    "✅ **선임 가능 조건**\n👉 승강기 관리/기술/직무 교육 이수\n👉 또는 **'승강기 운행 기본교육'** 이수만으로도 선임이 가능합니다.")
            return kakao_res([{"basicCard": {"title": "일반 자격 진단 결과", "description": desc, "buttons": [
                {"action": "webLink", "label": "✍️ 선임 신고하러 가기", "webLinkUrl": "https://minwon.koelsa.or.kr/"},
                {"action": "webLink", "label": "❓ 선임 신청 방법 안내", "webLinkUrl": "https://youtu.be/gtMaxkUw4cc?si=00Rl407_MMy1iNZS"}]}}])

        # =========================================================
        # 고유번호 7자리 기반 서비스
        # =========================================================
        if len(elv_no) == 7:
            info = get_info(elv_no)
            cmd = utterance.replace(elv_no, "").strip()

            # =====================================================
            # 👤 안전관리자 현황 조회
            # =====================================================
            if "안전관리자" in cmd:
                root = get_api(URLS['SAFE'], {'serviceKey': KEY, 'elevator_no': elv_no})
                name, end_de = "미등록", "정보없음"
                if root is not None:
                    items = root.findall('.//item')
                    if items:
                        m = items[-1]
                        name = m.findtext('safeMngrNm') or m.findtext('shuttleMngrNm') or "성함미상"
                        end_de = format_dt(m.findtext('valdEndDt') or m.findtext('eduEndDe'))
                desc = (f"✅ [안전관리자 선임 확인]\n"
                        f"건물명: {info['buldNm']}\n"
                        f"👤 성함: {name}\n"
                        f"🎓 만료: {end_de}")
                
                return kakao_res([{
                    "basicCard": {
                        "title": "📜 법적 의무사항 이행 확인",
                        "description": desc,
                        "buttons": [
                            {"action": "message", "label": "⏰ 선임 기한 안내", "messageText": f"언제까지 선임해야 하나요"},
                            {"action": "message", "label": "🎓 교육 이수 기준", "messageText": f"교육은 언제까지 받나요"},
                            {"action": "message", "label": "📋 안전관리자 직무 조회", "messageText": f"{elv_no} 법정직무조회1"} # 명칭 수정
                        ]
                    }
                }])
                
            # =====================================================
            # 📜 보험·점검 관련 법령 처음 화면
            # =====================================================
            if "보험및점검" in cmd:
                return kakao_res([{
                    "basicCard": {
                        "title": "📜 보험·점검 관련 법령",
                        "description": "궁금하신 항목을 선택해주세요.",
                        "buttons": [
                            {"action": "message", "label": "🛡️ 보험 가입 필수인가요", "messageText": f"{elv_no} 보험질문"},
                            {"action": "message", "label": "🛠️ 자체점검 필수인가요", "messageText": f"{elv_no} 점검질문"},
                            {"action": "message", "label": "↪️ 처음으로 돌아가기", "messageText": f"{elv_no}"}
                        ]
                    }
                }])

            # =========================================================
            # 🛡️ 보험 가입 질문
            # =========================================================
            if "보험질문" in cmd:
                return kakao_res([{
                    "basicCard": {
                        "title": "🛡️ 보험 가입 필수인가요",
                        "description": "승강기 사고배상 책임보험은 법적 의무입니다.",
                        "buttons": [
                            {"action": "message", "label": "🔍 보험 가입 여부 조회", "messageText": f"{elv_no} 보험가입확인"},
                            {"action": "message", "label": "↪️ 돌아가기", "messageText": f"{elv_no} 보험및점검"}
                        ]
                    }
                }])

            # =========================================================
            # 🛡️ 보험 가입 조회
            # =========================================================
            if "보험가입" in cmd or "보험확인" in cmd:
                root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': elv_no, 'numOfRows': 999})
                    
                if root is not None:
                    items = root.findall('.//item')
                    page = int(re.search(r'페이지(\d+)', cmd).group(1)) if "페이지" in cmd else 1
                        
                    start = (page - 1) * 15
                    display = items[start:start + 15]
                        
                    cards = []
                    for i in range(0, len(display), 3):
                        btns = [{
                            "action": "message",
                            "label": f"{(it.findtext('installationPlace') or '-').strip()}({it.findtext('elevatorNo')})",
                            "messageText": f"{it.findtext('elevatorNo')} 보험결과"
                        } for it in display[i:i+3]]
                            
                        cards.append({
                            "title": f"🛡️ 보험 확인 ({start+i+1}~)",
                            "description": f"🏢 {info['buldNm']}",
                            "buttons": btns
                        })
                            
                    if len(items) > start + 15:
                        cards.append({
                            "title": "🚀 다음 리스트",
                            "buttons": [{
                                "action": "message",
                                "label": "➡️ 다음 보기",
                                "messageText": f"{elv_no} 보험확인 페이지{page+1}"
                            }]
                        })
                            
                return kakao_res([{"carousel": {"type": "basicCard", "items": cards}}])   
                    
            if "보험결과" in cmd:
                return kakao_res([{
                    "basicCard": {
                        "title": "🛡️ 보험 가입 여부 조회",
                        "description": make_insur_report(elv_no, info)
                    }
                }])
                                    
            # =====================================================
            # 🛠️ 자체점검 질문
            # =====================================================            
            if "점검질문" in cmd:
                return kakao_res([{
                    "basicCard": {
                        "title": "🛠️ 자체점검 필수인가요",
                        "description": "매월 1회 이상 자체점검은 필수입니다.",
                        "buttons": [
                            {"action": "message", "label": "🔍 최근 점검 일지 조회", "messageText": f"{elv_no} 자체점검확인"},
                            {"action": "message", "label": "↪️ 돌아가기", "messageText": f"{elv_no} 보험및점검"}
                        ]
                    }
                }])
                                    
            # =====================================================
            # 🛠️ 점검 리스트
            # =====================================================            
            if "자체점검" in cmd:
                root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': elv_no, 'numOfRows': 999})
                    
                if root is not None:
                    items = root.findall('.//item')
                    page = int(re.search(r'페이지(\d+)', cmd).group(1)) if "페이지" in cmd else 1
                        
                    start = (page - 1) * 15
                    display = items[start:start + 15]
                        
                    cards = []
                    for i in range(0, len(display), 3):
                        btns = [{
                            "action": "message",
                            "label": f"{(it.findtext('installationPlace') or '-').strip()}({it.findtext('elevatorNo')})",
                            "messageText": f"{it.findtext('elevatorNo')} 결과확인"
                        } for it in display[i:i+3]]
                        
                        cards.append({
                            "title": f"🛠️ 점검 확인 ({start+i+1}~)",
                            "description": f"🏢 {info['buldNm']}",
                            "buttons": btns
                        })
                        
                    if len(items) > start + 15:
                        cards.append({
                            "title": "🚀 다음 리스트",
                            "buttons": [{
                                "action": "message",
                                "label": "➡️ 다음 보기",
                                "messageText": f"{elv_no} 자체점검 페이지{page+1}"
                            }]
                        })
                        
                return kakao_res([{"carousel": {"type": "basicCard", "items": cards}}])
                
            if "결과확인" in cmd:
                return kakao_res([{
                    "basicCard": {
                        "title": "🔍 최근 점검 일지 조회",
                        "description": make_check_report(elv_no, info) 
                    }
                }])
                
        # =========================================================
        # ⚙️ 호기조회 (고유번호 뒤에 무조건, 삭제 금지)
        # =========================================================
            if "호기정보" in utterance:
                root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': elv_no, 'numOfRows': 999})
                if root is not None:
                    items = root.findall('.//item')
                    page = int(re.search(r'페이지(\d+)', utterance).group(1)) if "페이지" in utterance else 1
                    start = (page-1)*15
                    display = items[start:start+15]
                    cards = []
                    for i in range(0, len(display), 3):
                        sub = display[i:i+3]
                        btns = [{"action": "message", "label": f"{(it.findtext('installationPlace') or '-').strip()}({it.findtext('elevatorNo')})", "messageText": f"{it.findtext('elevatorNo')} 조회제원"} for it in sub]
                        cards.append({"title": f"🔢 리스트 ({start+i+1}~)", "description": f"🏢 {info['buldNm']}", "buttons": btns})
                    if len(items) > start+15: cards.append({"title": "🚀 다음 리스트", "buttons": [{"action": "message", "label": "➡️ 다음 15개 보기", "messageText": f"{elv_no} 호기정보 페이지{page+1}"}]})
                    return kakao_res([{"carousel": {"type": "basicCard", "items": cards}}])
                    
            if "조회제원" in utterance:
                spec_text = make_spec_report(elv_no, info)
                
                return kakao_res([{
                    "basicCard": {
                        "title": f"⚙️ {elv_no} 호기 상세제원",
                        "description": spec_text,
                        "buttons": [
                            {"action": "message","label": "📅 검사 적용 시점 확인","messageText": f"연도계산"} # 명칭 수정
                        ]
                    }
                }])

        # =========================================================
        # 안전관리자 직무 조회
        # =========================================================
            
            check_titles = ["", "기계실/제어반 잠금 확인", "기계실 온도/환기 확인", "버튼 작동상태 확인", "법정 부착물 확인", "비상통화장치 확인", "비상열쇠 보관 확인"]
            check_descs = ["", 
                "기계실 또는 제어반 잠금 상태\n\n기계실이 있는 경우 출입문 잠금을 확인하세요. MRL은 제어반 닫힘 상태를 확인합니다.",
                "기계실 온도 및 환기장치\n\n기계실 온도는 40도 이하를 유지해야 합니다.",
                "호출 및 등록버튼 작동 상태\n\n내외부 버튼 파손 및 작동 여부를 확인하세요.",
                "표준부착물 부착 상태\n\n검사합격증과 고유번호 7자리 훼손 여부를 확인하세요.",
                "비상통화장치 작동 상태\n\n갇힘 사고 시 외부와 연락 가능한지 체크하세요.",
                "비상열쇠 관리 상태\n\n지정된 위치에 비상열쇠가 보관 중인지 확인하세요."]
            bad_descs = ["",
                "🚨 기계실/제어반이 열려 있습니다! 즉시 잠금 조치하세요.",
                "🚨 기계실 온도가 높거나 환기가 안 됩니다! 환기 장치를 가동하세요.",
                "🚨 버튼 작동 불량입니다! 유지보수 업체에 수리를 요청하세요.",
                "🚨 합격증 또는 고유번호 훼손! 즉시 재부착하세요.",
                "🚨 비상통화 불능! 즉각 수리가 필요합니다.",
                "🚨 비상열쇠 부재! 지정 위치에 반드시 비치하세요."]

            for i in range(1, 7):
                if f"법정직무조회{i}" in utterance:
                    next_idx = i + 1
                    next_text = f"법정직무조회{next_idx}" if next_idx <= 6 else "법정직무조회7"
                    return kakao_res([{"basicCard": {"title": check_titles[i], "description": check_descs[i], "buttons": [
                        {"action": "message", "label": "✅ 양호", "messageText": f"{elv_no} {next_text}"},
                        {"action": "message", "label": "❌ 불량", "messageText": f"{elv_no} 법정직무불량{i}"}]}}])

            for i in range(1, 7):
                if f"법정직무불량{i}" in utterance:
                    next_text = f"법정직무조회{i+1}" if i+1 <= 6 else "법정직무조회7"
                    return kakao_res([{"basicCard": {"title": f"⚠️ {i}번 불량 안내", "description": bad_descs[i], "buttons": [
                        {"action": "message", "label": "🛠️ 조치 완료 (양호)", "messageText": f"{elv_no} {next_text}"}]}}])

            if "법정직무조회7" in utterance:
                root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': elv_no, 'numOfRows': 999})
                if root is not None:
                    items = root.findall('.//item')
                    page = int(re.search(r'페이지(\d+)', utterance).group(1)) if "페이지" in utterance else 1
                    start = (page-1)*15
                    display = items[start:start+15]
                    cards = []
                    for i in range(0, len(display), 3):
                        btns = [{"action": "message", "label": f"{(it.findtext('installationPlace') or '-').strip()}({it.findtext('elevatorNo')})", "messageText": f"{it.findtext('elevatorNo')} 직무보험"} for it in display[i:i+3]]
                        cards.append({"title": f"🛡️ 보험 확인 ({start+i+1}~)", "description": f"🏢 {info['buldNm']}", "buttons": btns})

                     # 다음 페이지가 있을 경우 추가
                    if len(items) > start+15: cards.append({"title": "🚀 다음 리스트", "buttons": [{"action": "message", "label": "➡️ 다음 보기", "messageText": f"{elv_no} 법정직무조회7 페이지{page+1}"}]})
                    return kakao_res([{"carousel": {"type": "basicCard", "items": cards}}])

            if "직무보험" in utterance:
                return kakao_res([{"basicCard": {"title": "🛡️ 보험 가입 여부 확인", "description": make_insur_report(elv_no, info), "buttons": [
                    {"action": "message", "label": "🔍 최근 점검 일지 조회", "messageText": f"{elv_no} 법정직무조회8"}]}}]) # 🔍 자체점검일지 확인 -> 명칭수정
                
            if "법정직무조회8" in utterance:
                root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': elv_no, 'numOfRows': 999})
                if root is not None:
                    items = root.findall('.//item')
                    page = int(re.search(r'페이지(\d+)', utterance).group(1)) if "페이지" in utterance else 1
                    start = (page - 1) * 15
                    display = items[start:start + 15]
                    cards = []
                    
                    for i in range(0, len(display), 3):
                        sub_items = display[i:i + 3]
                        btns = [{"action": "message","label": f"{(it.findtext('installationPlace') or '-').strip()}({it.findtext('elevatorNo')})","messageText": f"{it.findtext('elevatorNo')} 직무점검"} for it in sub_items]
                        cards.append({"title": f"📝 점검 확인 ({start + i + 1}~)","description": f"🏢 {info['buldNm']}","buttons": btns})
                        
                    # 다음 페이지가 있을 경우 추가
                    if len(items) > start + 15:cards.append({"title": "🚀 다음 리스트","buttons": [{"action": "message","label": "➡️ 다음 보기","messageText": f"{elv_no} 법정직무조회8 페이지{page + 1}"}]})
                    return kakao_res([{"carousel": {"type": "basicCard", "items": cards}}])

            if "직무점검" in utterance:
                selected_info = get_info(elv_no)
                return kakao_res([{"basicCard": {"title": "🔍 최근 점검 일지 조회","description": make_check_report(elv_no, selected_info),"buttons": [
                    {"action": "message", "label": "🏁 모든 확인 완료", "messageText": f"{elv_no} 최종마무리"}]}}])
                
            if "최종마무리" in utterance:
                return kakao_res([{"basicCard": {"title": "✨ 수고하셨습니다!","description": f"건물({info['buldNm']})의 모든 확인을 마쳤습니다. 👍\n\n지금까지의 체크 리스트는 참고하시어 주기적으로 확인 하시기 바랍니다. 😄",
                                                 "buttons": {"action": "message","label": "처음으로 돌아가기","messageText": f"{elv_no}"}]}}])
                
        # =========================================================
        # 메인 베이스 화면 (아래 수정 금지 구역 - 라벨명만 구조도 반영)
        # =========================================================
            return kakao_res([{"basicCard": {"title": f"🏢 {info['buldNm']}", "description": f"📍 주소: {info['addr']}", "buttons": [
                {"action": "message", "label": "👤 안전관리자 현황 조회", "messageText": f"{elv_no} 👤 안전관리자 현황 조회"},
                {"action": "message", "label": "📜 보험·점검 관련 법령", "messageText": f"{elv_no} 보험및점검"}, # 라벨 수정 및 발화어 통일
                {"action": "message", "label": "🚨 사고·고장 이력", "messageText": f"{elv_no} 🚨 사고/고장 신고조회"} # 라벨 수정
            ]}}])

        return kakao_res([{"simpleText": {"text": "❓ 고유번호 7자리를 입력해주세요."}}])
    except Exception as e:
        return kakao_res([{"simpleText": {"text": f"⚠️ 서버 오류: {str(e)}"}}])

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
