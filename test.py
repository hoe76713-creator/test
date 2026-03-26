from flask import Flask, request, jsonify
import requests
import xml.etree.ElementTree as ET
import datetime
import re
import os

app = Flask(__name__)

KEY = os.environ.get("API_KEY")

URLS = {
    'BULD': "http://apis.data.go.kr/B553664/BuldElevatorService/getBuldElvtrList",
    'SAFE': "http://apis.data.go.kr/B553664/ElevatorSafeMngrService/getSafeMngrList",
    'INSUR': "http://apis.data.go.kr/B553664/ElevatorInsuranceService/getElvtrInsurance",
    'CHECK': "http://apis.data.go.kr/B553664/ElevatorSelfCheckService/getSelfCheckList",
    'SPEC': "http://apis.data.go.kr/B553664/ElevatorOperationService/getOperationInfoListV1",
}

# ---------------- 공통 ----------------
def get_api(url, params):
    try:
        res = requests.get(url, params=params, timeout=5)
        if res.status_code == 200:
            return ET.fromstring(res.content.strip())
    except:
        return None
    return None

def format_dt(s):
    if not s:
        return "정보없음"
    s = s.replace("-", "")
    return f"{s[:4]}년 {s[4:6]}월 {s[6:]}일"

def kakao_res(outputs):
    return jsonify({"version": "2.0", "template": {"outputs": outputs}})

def simple(text):
    return kakao_res([{"simpleText": {"text": text}}])

# ---------------- 기본정보 ----------------
def get_info(no):
    root = get_api(URLS['BULD'], {'serviceKey': KEY, 'elevator_no': no})
    if root is not None and root.find('.//item') is not None:
        it = root.find('.//item')
        return {
            "buldNm": it.findtext('buldNm'),
            "addr": it.findtext('address1'),
            "asign": it.findtext('elvtrAsignNo')
        }
    return {"buldNm": "정보없음", "addr": "정보없음", "asign": "-"}

# ---------------- 리포트 ----------------
def make_insur(no):
    root = get_api(URLS['INSUR'], {
        'serviceKey': KEY,
        'elevator_no': no,
        'cont_ymd': datetime.datetime.now().strftime("%Y%m")
    })
    if root is not None and root.find('.//item') is not None:
        it = root.find('.//item')
        return f"🏢 보험사: {it.findtext('companyNm')}\n⏰ 만료일: {format_dt(it.findtext('contEnDe'))}"
    return "보험 정보 없음"

def make_check(no):
    result = []
    today = datetime.datetime.now()
    for i in range(3):
        ym = (today - datetime.timedelta(days=30*i)).strftime("%Y%m")
        root = get_api(URLS['CHECK'], {
            'serviceKey': KEY,
            'elevator_no': no,
            'yyyymm': ym
        })
        if root is not None and root.find('.//item') is not None:
            it = root.find('.//item')
            result.append(f"{ym[:4]}년 {ym[4:]}월\n결과: {it.findtext('selchkResultNm')}\n업체: {it.findtext('companyNm')}")
    return "\n\n".join(result) if result else "점검 이력 없음"

# ---------------- 메인 ----------------
@app.route('/ask', methods=['POST'])
def ask():
    try:
        content = request.get_json()
        utterance = content['userRequest']['utterance'].replace(" ", "")
        nums = re.findall(r'\d{7}', utterance)
        elv_no = nums[0] if nums else ""

        # ==================================================
        # 1️⃣ 안전관리자 조회
        # ==================================================
        if len(elv_no) == 7 and "법적의무" in utterance:
            info = get_info(elv_no)

            root = get_api(URLS['SAFE'], {'serviceKey': KEY, 'elevator_no': elv_no})
            name, end = "미등록", "정보없음"

            if root is not None:
                item = root.find('.//item')
                if item is not None:
                    name = item.findtext('safeMngrNm') or "성함미상"
                    end = format_dt(item.findtext('eduEndDe'))

            desc = f"건물명: {info['buldNm']}\n👤 {name}\n🎓 {end}"

            return kakao_res([{"basicCard": {
                "title": "⚖️ 법적 의무사항",
                "description": desc,
                "buttons": [
                    {"action": "message", "label": "선임 기한", "messageText": "언제까지선임해야하나요"},
                    {"action": "message", "label": "교육 기준", "messageText": "교육은언제까지받나요"},
                    {"action": "message", "label": "점검 시작", "messageText": f"{elv_no} 법정직무조회1"}
                ]
            }}])

        if "언제까지선임해야하나요" in utterance:
            return simple("3개월 이내 선임 필요")

        if "교육은언제까지받나요" in utterance:
            return simple("선임 후 3개월 내 교육 / 이후 3년 주기")

        # ==================================================
        # 2️⃣ 법정직무 점검 1~6
        # ==================================================
        titles = ["", "잠금 확인", "온도 확인", "버튼 확인", "부착물 확인", "비상통화", "비상열쇠"]
        bads = ["", "잠금 필요", "온도 이상", "버튼 고장", "훼손", "통화 불가", "열쇠 없음"]

        for i in range(1, 7):
            if f"법정직무조회{i}" in utterance:
                next_step = f"법정직무조회{i+1}" if i < 6 else "법정직무조회7"
                return kakao_res([{"basicCard": {
                    "title": titles[i],
                    "description": titles[i],
                    "buttons": [
                        {"action": "message", "label": "양호", "messageText": f"{elv_no} {next_step}"},
                        {"action": "message", "label": "불량", "messageText": f"{elv_no} 법정직무불량{i}"}
                    ]
                }}])

            if f"법정직무불량{i}" in utterance:
                next_step = f"법정직무조회{i+1}" if i < 6 else "법정직무조회7"
                return kakao_res([{"basicCard": {
                    "title": "불량 안내",
                    "description": bads[i],
                    "buttons": [
                        {"action": "message", "label": "조치 완료", "messageText": f"{elv_no} {next_step}"}
                    ]
                }}])

        # ==================================================
        # 3️⃣ 보험 → 점검 → 종료
        # ==================================================
        if "법정직무조회7" in utterance:
            return simple("👉 여기서 호기 리스트 출력 (추가 가능)")

        if "법정직무조회8" in utterance:
            info = get_info(elv_no)
            return simple(make_insur(elv_no))

        if "법정직무조회9" in utterance:
            return simple(make_check(elv_no))

        if "최종마무리" in utterance:
            return simple("✨ 점검 완료 수고하셨습니다!")

        # ==================================================
        # 4️⃣ 보험 / 점검 단독 조회
        # ==================================================
        if utterance.startswith("보험가입_"):
            no = utterance.split("_")[1]
            return simple(make_insur(no))

        if utterance.startswith("자체점검_"):
            no = utterance.split("_")[1]
            return simple(make_check(no))

        # ==================================================
        # 5️⃣ 정밀검사
        # ==================================================
        if utterance.startswith("정밀검사_설치연도_"):
            year = int(utterance.split("_")[2])
            return simple(f"{year+15} / {year+18} / {year+21}")

        if utterance.startswith("정밀검사_번호조회_"):
            no = utterance.split("_")[2]
            root = get_api(URLS['SPEC'], {'serviceKey': KEY, 'elevator_no': no})
            if root is not None and root.find('.//item') is not None:
                it = root.find('.//item')
                install = it.findtext('installationDe')
                if install:
                    year = int(install[:4])
                    return simple(f"{year+15} / {year+18} / {year+21}")
            return simple("설치정보 없음")

        return simple("❓ 이해하지 못했습니다.")

    except Exception as e:
        return simple(f"오류: {str(e)}")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
