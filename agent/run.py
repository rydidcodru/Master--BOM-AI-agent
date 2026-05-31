"""BOM 트리 파일럿 빌드 및 검증 CLI 프로그램 (1단계 및 2단계)."""
import argparse
import sys
from pathlib import Path
from typing import Optional

from config import get_neo4j_driver
from bom_tree import parse_bom_excel, InMemoryBOMRepository, Neo4jBOMRepository
from ppt_parser import parse_pptx_file
from query_agent import process_change_request, match_target_node_with_llm
from compact_oven_processor import run_pipeline


def build_and_verify_tree(bom_path: Path, use_neo4j: bool) -> InMemoryBOMRepository:
    print("=" * 60)
    print(f"BOM Tree Parser - 파일럿 검증 (1단계)")
    print("=" * 60)
    
    print(f"\n[1] BOM 엑셀 파일 로딩 중: {bom_path.name}")
    if not bom_path.exists():
        print(f"[오류] 파일이 존재하지 않습니다: {bom_path}")
        sys.exit(1)
        
    try:
        nodes = parse_bom_excel(bom_path)
    except Exception as e:
        print(f"[오류] BOM 파싱 실패: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    print(f"  - 파싱 성공! 총 {len(nodes):,}개 노드 추출됨")

    # In-memory 저장소 빌드
    print(f"\n[2] In-Memory (NetworkX) 그래프 구축 중...")
    in_mem_repo = InMemoryBOMRepository()
    in_mem_repo.clear()
    
    for node in nodes:
        in_mem_repo.save_node(node)
        
    for node in nodes:
        if node.parent_part_no:
            in_mem_repo.save_relationship(node.parent_part_no, node.part_no)

    print(f"  - In-Memory 구축 성공! 그래프 노드 수: {in_mem_repo.graph.number_of_nodes()}개, 엣지 수: {in_mem_repo.graph.number_of_edges()}개")

    # Neo4j 저장소 빌드 (옵션)
    if use_neo4j:
        print(f"\n[3] Neo4j 그래프 데이터베이스 적재 시도 중...")
        try:
            neo_repo = Neo4jBOMRepository(get_neo4j_driver)
            print("  - Neo4j 연결 확인 중...")
            neo_repo.clear()
            print("  - 기존 Neo4j 데이터 초기화 완료")
            
            print("  - 노드 적재 중...")
            for node in nodes:
                neo_repo.save_node(node)
                
            print("  - 계층 관계(엣지) 연결 중...")
            for node in nodes:
                if node.parent_part_no:
                    neo_repo.save_relationship(node.parent_part_no, node.part_no)
            print("  - Neo4j 적재 완료!")
        except Exception as e:
            print(f"  - [경고] Neo4j 적재 실패 (Neo4j 컨테이너가 켜져 있는지 확인하세요): {e}")

    # 루트 노드 확인
    root_nodes = [n for n in nodes if n.lvl == 0]
    if not root_nodes:
        root_nodes = [n for n in nodes if n.parent_part_no is None]
    if root_nodes:
        print(f"  - 루트 노드 식별: {root_nodes[0].part_no} ({root_nodes[0].part_name[:30]})")

    return in_mem_repo


def verify_ppt_and_expansion(ppt_path: Path, query_text: Optional[str], repo: InMemoryBOMRepository, part_text: Optional[str] = None) -> None:
    print("\n" + "=" * 60)
    print(f"PPT 파싱 및 에이전트 쿼리 확장 검증 (2단계)")
    print("=" * 60)

    change_items = []
    
    # 1) PPT 파싱 진행 (선택사항)
    if ppt_path:
        print(f"\n[1] 심의회 PPT 파싱 중: {ppt_path.name}")
        if not ppt_path.exists():
            print(f"[오류] PPT 파일이 존재하지 않습니다: {ppt_path}")
            sys.exit(1)
        try:
            ppt_data = parse_pptx_file(ppt_path)
            
            # 프로젝트 메타데이터 출력
            meta = ppt_data.get("project_meta", {})
            print(f"  - 프로젝트명: {meta.get('project_name')}")
            print(f"  - Base 모델: {meta.get('base_model')} | 개발 등급: {meta.get('dev_grade')}")
            print(f"  - 제품군: {meta.get('product_type')} | 개발 유형: {meta.get('dev_type')}")
            
            # 상세 변경점 출력
            changes = ppt_data.get("detailed_changes", [])
            print(f"  - 파싱된 상세 변경점 수: {len(changes)}개")
            for idx, chg in enumerate(changes[:5], 1):
                item_str = f"[{chg.get('part')}] {chg.get('change_detail')} (사유: {chg.get('change_reason')})"
                print(f"    * 변경점 {idx}: {item_str[:100]}")
                change_items.append(chg)
                
            if len(changes) > 5:
                print(f"    ...외 {len(changes) - 5}개의 변경점 생략")
                
        except Exception as e:
            print(f"[오류] PPT 파싱 실패: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)

    # 2) 쿼리 확장 분석 진행
    selected_query = query_text
    selected_part = part_text
    
    if not selected_query and change_items:
        selected_query = change_items[0].get("change_detail")
        selected_part = change_items[0].get("part")
        print(f"\n[알림] 입력 질의어가 없으므로 PPT 첫 번째 변경점을 분석합니다: '{selected_query}'")
        
    if not selected_query:
        selected_query = "도어에 카메라 추가"
        selected_part = "Cover, Camera"
        print(f"\n[알림] 입력 및 파싱된 쿼리가 없으므로 가상 시나리오를 사용합니다: '{selected_query}'")

    print(f"\n[2] 에이전트 쿼리 확장 및 검증 시작")
    try:
        expanded, verify_res = process_change_request(selected_query)
    except Exception as e:
        print(f"[오류] LLM 에이전트 호출 실패 (API 키 설정을 확인하세요): {e}")
        return

    print(f"\n  - 쿼리 확장 결과 (최종):")
    print(f"    * 타겟 어셈블리: {expanded.get('target_assembly')}")
    print(f"    * 타겟 동의어: {expanded.get('synonyms')}")
    print(f"    * 연관 부품 키워드: {expanded.get('related_keywords')}")
    print(f"    * 행동 방식 (Action): {expanded.get('action')}")
    
    print(f"\n  - 에이전트 자가 검증 결과:")
    print(f"    * 유효 여부: {verify_res.get('is_valid')}")
    print(f"    * 검증 의견: {verify_res.get('reason')}")

    # 3) 트리 기반 탐색 연동 검증 (3단계 LLM 정밀 매칭 적용)
    print(f"\n[3] 쿼리 확장 단어를 이용한 BOM 트리 연동 매칭 테스트 (3단계 고도화)")
    target_assembly_name = expanded.get("target_assembly", "Door")
    print(f"  - 1단계 빌드된 BOM 트리에서 '{target_assembly_name}' 관련 후보 노드 탐색 중...")
    
    # 동의어 및 타겟 어셈블리명을 활용한 1차 키워드 후보군 추출
    keywords_to_try = [target_assembly_name] + expanded.get("synonyms", [])
    matched_nodes = []
    seen_part_nos = set()
    
    for kw in keywords_to_try:
        if not kw:
            continue
        nodes = repo.find_nodes_by_keyword(kw)
        for n in nodes:
            if n.part_no not in seen_part_nos:
                matched_nodes.append(n)
                seen_part_nos.add(n.part_no)
                
    if matched_nodes:
        print(f"    * 1차 키워드 탐색 성공: 총 {len(matched_nodes)}개 후보군 노드 식별됨.")
        
        # 후보 노드의 메타데이터 구성 (품번, 품명, 레벨, 공급유형, 어셈블리 여부 등)
        candidates_meta = []
        for n in matched_nodes:
            successors = list(repo.graph.successors(n.part_no))
            candidates_meta.append({
                "part_no": n.part_no,
                "part_name": n.part_name,
                "lvl": n.lvl,
                "parent_part_no": n.parent_part_no,
                "supply_type": n.supply_type,
                "is_assembly": len(successors) > 0,
                "child_count": len(successors)
            })
            
        # 3단계: LLM을 이용한 정밀 노드 매칭 및 스코어링
        print("  - 후보군 메타데이터와 가이드라인 기반 LLM 정밀 노드 매칭 분석 실행...")
        match_res = match_target_node_with_llm(candidates_meta, selected_query, selected_part)
        matched_part_no = match_res.get("matched_part_no")
        score = match_res.get("score", 0)
        reason = match_res.get("reason", "")
        
        if matched_part_no:
            matched_node = repo.get_node(matched_part_no)
            print(f"\n  - 3단계 LLM 정밀 매칭 결과:")
            print(f"    * 최종 매칭 노드 품번: {matched_part_no}")
            print(f"    * 부품명: {matched_node.part_name}")
            print(f"    * 매칭 점수: {score}점")
            print(f"    * 매칭 사유: {reason}")
            
            # 하위 트리 구조 확인
            successors = list(repo.graph.successors(matched_part_no))
            print(f"    * 직속 하위 자식 노드 수: {len(successors)}개")
            print(f"    * 전체 서브트리 노드 수: {len(repo.get_sub_tree(matched_part_no))}개")
        else:
            print("    * [경고] LLM이 적절한 매칭 대상을 후보 리스트에서 선정하지 못했습니다.")
    else:
        print("    * [오류] BOM 트리에서 확장 키워드에 부합하는 1차 후보군 노드를 전혀 찾지 못했습니다.")

    print("\n" + "=" * 60)
    print("3단계 고도화 검증 완료.")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="BOM 트리 및 에이전트 파일럿 실행기")
    parser.add_argument(
        "--bom",
        type=str,
        default="../legacy/data/uploads/base_bom.xlsx",
        help="파싱할 Base BOM 엑셀 파일 경로"
    )
    parser.add_argument(
        "--ppt",
        type=str,
        default="",
        help="파싱할 심의회 PPTX 파일 경로 (선택 사항)"
    )
    parser.add_argument(
        "--query",
        type=str,
        default="",
        help="에이전트 쿼리 확장을 테스트할 질의어 (선택 사항)"
    )
    parser.add_argument(
        "--neo4j",
        action="store_true",
        help="Neo4j 그래프 DB 적재 활성화 여부"
    )
    parser.add_argument(
        "--part",
        type=str,
        default="",
        help="에이전트 쿼리 확장에 적용할 PPT 파트 명칭 (선택 사항)"
    )
    parser.add_argument(
        "--compact-oven",
        action="store_true",
        help="Compact Oven 마스터 엑셀 자동 업데이트 파이프라인 구동"
    )
    args = parser.parse_args()
    
    if args.compact_oven:
        run_pipeline()
        sys.exit(0)
    
    # 1단계 실행
    repo = build_and_verify_tree(Path(args.bom), args.neo4j)
    
    # 2단계 실행
    ppt_path = Path(args.ppt) if args.ppt else None
    verify_ppt_and_expansion(ppt_path, args.query if args.query else None, repo, args.part if args.part else None)


if __name__ == "__main__":
    main()
