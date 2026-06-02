"""LLM 기반 쿼리 확장 및 자가 검증을 수행하는 에이전트 모듈 (API 미지정 시 모크 모드 지원)."""
import json
import re
from typing import Any, Dict, List, Tuple, Optional

from config import get_llm_client


def _clean_json_response(text: str) -> str:
    """LLM이 반환한 텍스트에서 마크다운 JSON 블록 등을 제거하고 순수 JSON 문자열만 추출합니다."""
    s = text.strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", s, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return s


def expand_query(query_text: str) -> Dict[str, Any]:
    """사용자의 자연어 변경 요청 쿼리를 확장하여 타겟 어셈블리, 동의어, 연관 부품 키워드 등을 도출합니다."""
    try:
        client, params = get_llm_client()
        
        system_prompt = (
            "당신은 가전제품(오븐, 전자레인지 등)의 BOM(Bill of Materials) 설계 전문가이자 엔지니어링 에이전트입니다.\n"
            "사용자가 입력한 변경 요청을 분석하여 검색 재현율(Recall)과 정확도(Precision)를 높이기 위해 구조화된 확장 질의 데이터를 생성합니다.\n\n"
            "BOM 도메인 설계 규칙:\n"
            "- UIT(User Item Type)가 G(국내구입) 또는 D(해외도입)인 품목은 하위 구조를 전개하지 않고 협력사로부터 모듈 전체를 사입합니다. 따라서 내부 부품이 교체되더라도 BOM에서는 상위 모듈 품번 자체를 교체(REPLACE)해야 합니다.\n"
            "- UIT가 T(사내자작), P(사내공정) 또는 S, M, R(협력사 임가공 조립품)인 Make 품목은 하부 자재 구조를 발주/사급 전개하므로, 상위 어셈블리는 유지하고 하부의 특정 단품만 핀포인트로 추가(ADD), 수정(MODIFY), 삭제(DELETE)하는 것이 가능합니다.\n\n"
            "반드시 아래 JSON 형식을 엄격히 지켜 응답해 주세요. 추가 텍스트나 설명은 생략하십시오.\n"
            "{\n"
            '  "target_assembly": "가장 핵심이 되는 최상위 변경 타겟 어셈블리명 (예: Door Assembly, Cavity, Controller Assembly)",\n'
            '  "synonyms": ["타겟 객체에 대한 한글 및 영어 동의어 리스트 (예: 도어, 문, door, outer door)"],\n'
            '  "related_keywords": ["변경에 따라 함께 교체되거나 추가되어야 하는 관련 하부 부품 및 자재 키워드 리스트 (예: camera, harness, bracket, LED, cable, lens)"],\n'
            '  "action": "변경 행동 방식 유형 (ADD, MODIFY, DELETE, REPLACE 중 하나)"\n'
            "}"
        )
        
        user_prompt = f"사용자 변경 요청: '{query_text}'"
        
        chat_kwargs = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            **params
        }
        
        try:
            chat_kwargs["response_format"] = {"type": "json_object"}
        except Exception:
            pass
            
        print(f"\n[DEBUG] LLM 요청 프롬프트 (User):\n{user_prompt}")
        res = client.chat.completions.create(**chat_kwargs)
        raw_content = res.choices[0].message.content or "{}"
        print(f"[DEBUG] LLM Raw 응답:\n{raw_content}\n")
        cleaned_content = _clean_json_response(raw_content)
        
        return json.loads(cleaned_content)
        
    except Exception as e:
        # API 오류 또는 설정 유실 시 파일럿 테스트를 위한 모크(Mock) 폴백 적용
        print(f"  - [알림] API 미설정 혹은 호출 에러로 인해 파일럿 테스트용 모크(Mock) 확장을 실행합니다. (사유: {e})")
        q_lower = query_text.lower()
        if "camera" in q_lower or "카메라" in q_lower or "도어" in q_lower:
            return {
                "target_assembly": "Door Assembly",
                "synonyms": ["도어", "문", "door", "outer door", "glass door"],
                "related_keywords": ["camera", "harness", "bracket", "LED", "cable", "lens", "holder"],
                "action": "ADD"
            }
        elif "panel" in q_lower or "패널" in q_lower or "rear" in q_lower:
            return {
                "target_assembly": "Rear Panel Assembly",
                "synonyms": ["패널", "rear panel", "후면 패널", "back cover"],
                "related_keywords": ["screw", "bracket", "cover", "insulator"],
                "action": "MODIFY"
            }
        elif "stopper" in q_lower or "스토퍼" in q_lower:
            return {
                "target_assembly": "Door Assembly",
                "synonyms": ["stopper", "스토퍼", "도어 스토퍼"],
                "related_keywords": ["screw", "bracket", "stopper"],
                "action": "MODIFY"
            }
        elif "motor" in q_lower or "모터" in q_lower or "bldc" in q_lower or "ac" in q_lower:
            return {
                "target_assembly": "Motor Assembly",
                "synonyms": ["motor", "모터", "fan motor", "대류 모터", "conv motor"],
                "related_keywords": ["fan", "blade", "bracket", "harness", "screw", "shaft"],
                "action": "REPLACE"
            }
        else:
            return {
                "target_assembly": "Controller Assembly",
                "synonyms": ["제어", "컨트롤러", "PCB", "PBA"],
                "related_keywords": ["display", "button", "harness", "screw", "LED"],
                "action": "MODIFY"
            }


def verify_expansion(expansion_result: Dict[str, Any], original_query: str) -> Dict[str, Any]:
    """확장된 쿼리 결과가 원래 사용자의 변경 의도와 기술적 정합성에 부합하는지 LLM을 통해 자가 검증(Self-Verification)을 수행합니다."""
    try:
        client, params = get_llm_client()
        
        system_prompt = (
            "당신은 가전제품 BOM 설계 타당성을 검토하는 수석 엔지니어 검증원입니다.\n"
            "사용자가 제시한 '원래 변경 요청'과 다른 에이전트가 도출한 '쿼리 확장 결과'를 비교 평가하십시오.\n\n"
            "BOM 도메인 설계 규칙:\n"
            "- UIT가 G(국내구입) 또는 D(해외도입)인 품목은 하위 구조를 전개하지 않는 Buy 품목이므로, 내부 부품 변경 시 상위 모듈 전체가 교체(REPLACE)되는 것이 옳습니다.\n"
            "- UIT가 T, P 또는 S, M, R인 Make 품목은 하부 자재 구조를 사급 전개하므로, 하부 단품의 개별 핀포인트 추가(ADD)/삭제(DELETE)/수정(MODIFY)이 가능합니다.\n\n"
            "검증 기준:\n"
            "1. 타겟 어셈블리(target_assembly)가 원래 변경 대상에 부합하는지 여부.\n"
            "2. 동의어(synonyms)가 적절하게 한국어/영어로 구성되어 있는지 여부.\n"
            "3. 연관 부품 키워드(related_keywords)에 쿼리와 무관한 엉뚱한 부품이 들어가 있지 않은지 여부.\n"
            "4. 행동 유형(action)이 위의 BOM 도메인 설계 규칙(Buy는 REPLACE, Make는 ADD/MODIFY/DELETE 가능)에 모순되지 않고 부합하는지 여부.\n\n"
            "반드시 아래 JSON 형식을 엄격히 준수하여 평가 결과를 출력하십시오. 추가적인 코멘트는 포함하지 마십시오.\n"
            "{\n"
            '  "is_valid": 검증 기준에 모두 부합하면 true, 오류나 수정이 필요하면 false (boolean),\n'
            '  "reason": 검증 결과 판단 근거 설명 (한글로 간결히 기술),\n'
            '  "refined_expansion": 만약 is_valid가 false인 경우, 원본 쿼리 확장 결과를 올바르게 수정한 정제된 JSON 객체를 여기에 포함 (is_valid가 true인 경우 null로 지정)\n'
            "}"
        )
        
        user_prompt = (
            f"원래 변경 요청: '{original_query}'\n\n"
            f"쿼리 확장 결과 JSON:\n{json.dumps(expansion_result, ensure_ascii=False)}"
        )
        
        chat_kwargs = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            **params
        }
        
        try:
            chat_kwargs["response_format"] = {"type": "json_object"}
        except Exception:
            pass
            
        print(f"\n[DEBUG] 자가 검증 LLM 요청 데이터:\n{user_prompt}")
        res = client.chat.completions.create(**chat_kwargs)
        raw_content = res.choices[0].message.content or "{}"
        print(f"[DEBUG] 자가 검증 LLM Raw 응답:\n{raw_content}\n")
        cleaned_content = _clean_json_response(raw_content)
        
        return json.loads(cleaned_content)
        
    except Exception:
        # API 미설정 시 모킹 검증
        return {
            "is_valid": True,
            "reason": "파일럿 모크 검증: 확장 결과가 적절히 원래 의도에 부합합니다.",
            "refined_expansion": None
        }


def process_change_request(query_text: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """쿼리 확장 및 자가 검증을 연쇄적으로 실행하여 최종 정제된 쿼리 확장 결과를 반환합니다."""
    print(f"  - [LLM] 1단계: '{query_text}' 쿼리 확장 분석 중...")
    expanded = expand_query(query_text)
    
    print(f"  - [LLM] 2단계: 확장 결과 자가 검증(Self-Verification) 가드레일 가동 중...")
    verification = verify_expansion(expanded, query_text)
    
    final_expanded = expanded
    if not verification.get("is_valid", True) and verification.get("refined_expansion"):
        print(f"  - [알림] 검증 단계에서 쿼리 보정 탐지됨: {verification.get('reason')}")
        final_expanded = verification["refined_expansion"]
        
    return final_expanded, verification


def match_target_node_with_llm(
    candidates_meta: List[Dict[str, Any]],
    query_text: str,
    part_info: Optional[str] = None
) -> Dict[str, Any]:
    """BOM 1차 검색 후보군 노드 목록과 사용자 변경점 및 PPT 파트 정보를 종합하여 최적의 타겟 노드를 선정합니다."""
    if not candidates_meta:
        return {
            "matched_part_no": None,
            "score": 0,
            "reason": "후보 노드 목록이 비어 있습니다."
        }

    try:
        client, params = get_llm_client()

        system_prompt = (
            "당신은 가전제품의 설계 도면과 BOM 구조를 검토하는 정밀 매칭 AI 에이전트입니다.\n"
            "사용자의 변경 요청 및 설계 파트 정보와 일치하는 가장 타당한 변경 대상 노드를 후보 리스트에서 선정해야 합니다.\n\n"
            "매칭 기준:\n"
            "1. PPT 추출 Part 정보와 품명(part_name) 간의 용어적 일치성과 기능적 유사성을 최우선으로 고려하십시오. (예: 'Conv. Motor'는 대류 팬용 모터이므로 'AC, Fan' 혹은 'Convection'이 품명에 명확히 들어간 품번을 골라야 하며, 기능이 다른 서브 모터나 브라켓, 무관한 부품들은 배제합니다.)\n"
            "2. BOM 도메인 설계 규칙:\n"
            "   - UIT(User Item Type)가 G(국내구입) 또는 D(해외도입)인 품목은 하위 구조를 시스템에서 전개하지 않고 협력사로부터 모듈 전체를 사입합니다. 따라서 내부 부품이 교체되더라도 BOM에서는 상위 모듈 품번 자체를 교체(REPLACE)해야 합니다.\n"
            "   - UIT가 T(사내자작), P(사내공정) 또는 S, M, R(협력사 임가공 조립품)인 Make 품목은 하부 자재 구조를 직접 발주/사급 전개하므로, 상위 어셈블리는 유지하고 하부의 특정 단품만 핀포인트로 추가(ADD), 수정(MODIFY), 삭제(DELETE)할 수 있습니다.\n\n"
            "반드시 아래 JSON 형식을 엄격히 준수하여 최종 매칭 결과를 출력하십시오. 추가적인 코멘트는 포함하지 마십시오.\n"
            "{\n"
            '  "matched_part_no": "최종 매칭된 최적의 품번 (후보 리스트에 존재하는 part_no 중 하나)",\n'
            '  "score": 0~100 사이의 정수 점수 (신뢰도 점수),\n'
            '  "reason": "이 노드를 최종 선정한 도메인/논리적 근거 (한글로 간결히 기재)"\n'
            "}"
        )

        user_prompt = (
            f"사용자 변경 요청: '{query_text}'\n"
            f"PPT 추출 Part 정보: '{part_info or '없음'}'\n\n"
            f"매칭 후보 노드 목록:\n{json.dumps(candidates_meta, ensure_ascii=False, indent=2)}"
        )

        chat_kwargs = {
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            **params
        }

        try:
            chat_kwargs["response_format"] = {"type": "json_object"}
        except Exception:
            pass

        print(f"\n[DEBUG] 3단계 LLM 노드 매칭 요청 데이터:\n{user_prompt}")
        res = client.chat.completions.create(**chat_kwargs)
        raw_content = res.choices[0].message.content or "{}"
        print(f"[DEBUG] 3단계 LLM 노드 매칭 Raw 응답:\n{raw_content}\n")
        cleaned_content = _clean_json_response(raw_content)

        return json.loads(cleaned_content)

    except Exception as e:
        # API 오류 또는 설정 유실 시 파일럿 테스트를 위한 모크(Mock) 매칭 폴백
        print(f"  - [알림] API 미설정 혹은 호출 에러로 인해 3단계 모크(Mock) 매칭을 실행합니다. (사유: {e})")
        q_lower = query_text.lower()
        part_lower = (part_info or "").lower()

        # 1) 모터 변경점 시나리오 (Conv. Motor -> EAU65078502 어셈블리 타겟)
        if "motor" in q_lower or "모터" in q_lower or "motor" in part_lower:
            target_no = "EAU65078502"
            # 후보 노드 중 해당 번호가 있으면 매칭
            exists = any(c["part_no"] == target_no for c in candidates_meta)
            if exists:
                return {
                    "matched_part_no": target_no,
                    "score": 95,
                    "reason": "모터 구동부 전체가 AC에서 BLDC로 사양 변경되는 대형 변경점이므로, 해당 구형 모터 어셈블리 EAU65078502를 매칭합니다."
                }
            
        # 2) 팬 변경점 시나리오 (Conv. Fan -> 5900W1N004D 단품 타겟)
        if "fan" in q_lower or "팬" in q_lower or "fan" in part_lower:
            target_no = "5900W1N004D"
            exists = any(c["part_no"] == target_no for c in candidates_meta)
            if exists:
                return {
                    "matched_part_no": target_no,
                    "score": 98,
                    "reason": "용접 타입 팬 변경에 따라 형상 변경이 발생하는 실제 대류 팬 단품 노드 5900W1N004D를 핀포인트 매칭합니다."
                }

        # 3) 기본 첫 번째 후보 반환
        if candidates_meta:
            return {
                "matched_part_no": candidates_meta[0]["part_no"],
                "score": 50,
                "reason": "대응되는 모크 룰이 없어 목록의 첫 번째 후보 노드를 기본 선택합니다."
            }
        else:
            return {
                "matched_part_no": None,
                "score": 0,
                "reason": "후보 노드 목록이 비어 있습니다."
            }
