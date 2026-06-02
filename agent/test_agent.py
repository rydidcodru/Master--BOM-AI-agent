import unittest
from pathlib import Path
from bom_tree import parse_bom_excel, InMemoryBOMRepository
from query_agent import expand_query, verify_expansion, match_target_node_with_llm


class TestBOMAgent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # 테스트용 실제 BOM 트리 로드
        bom_path = Path(__file__).parent / "../legacy/data/uploads/base_bom.xlsx"
        cls.repo = InMemoryBOMRepository()
        cls.nodes = parse_bom_excel(bom_path)
        for n in cls.nodes:
            cls.repo.save_node(n)
        for n in cls.nodes:
            if n.parent_part_no:
                cls.repo.save_relationship(n.parent_part_no, n.part_no)

    def test_01_expand_query(self):
        """1단계: 쿼리 확장 분석 동작 테스트"""
        res = expand_query("AC motor → BLDC motor")
        self.assertIn("target_assembly", res)
        self.assertIn("synonyms", res)
        self.assertIn("related_keywords", res)
        self.assertIn("action", res)
        print(f"\n[Test] expand_query Result: {res}")

    def test_02_verify_expansion(self):
        """2단계: 확장 결과 자가 검증 테스트"""
        expansion = {
            "target_assembly": "Motor",
            "synonyms": ["motor", "모터"],
            "related_keywords": ["harness", "bracket"],
            "action": "REPLACE"
        }
        res = verify_expansion(expansion, "AC motor → BLDC motor")
        self.assertIn("is_valid", res)
        self.assertIn("reason", res)
        print(f"\n[Test] verify_expansion Result: {res}")

    def test_03_match_motor_scenario(self):
        """3단계: 모터 변경점 시나리오 LLM 정밀 매칭 테스트"""
        keywords = ["Motor", "모터", "AC motor"]
        matched_nodes = []
        seen = set()
        for kw in keywords:
            nodes = self.repo.find_nodes_by_keyword(kw)
            for n in nodes:
                if n.part_no not in seen:
                    matched_nodes.append(n)
                    seen.add(n.part_no)

        candidates_meta = []
        for n in matched_nodes:
            successors = list(self.repo.graph.successors(n.part_no))
            candidates_meta.append({
                "part_no": n.part_no,
                "part_name": n.part_name,
                "lvl": n.lvl,
                "parent_part_no": n.parent_part_no,
                "supply_type": n.supply_type,
                "is_assembly": len(successors) > 0,
                "child_count": len(successors)
            })

        res = match_target_node_with_llm(candidates_meta, "AC motor → BLDC motor", "Conv. Motor")
        self.assertIsNotNone(res.get("matched_part_no"))
        self.assertIn(res.get("matched_part_no"), ["EAU65078502", "EAU62343003"])
        self.assertGreaterEqual(res.get("score", 0), 80)
        print(f"\n[Test] Motor Scenario Match Result: {res}")

    def test_04_match_fan_scenario(self):
        """3단계: 팬 변경점 시나리오 LLM 정밀 매칭 테스트"""
        keywords = ["Fan", "팬"]
        matched_nodes = []
        seen = set()
        for kw in keywords:
            nodes = self.repo.find_nodes_by_keyword(kw)
            for n in nodes:
                if n.part_no not in seen:
                    matched_nodes.append(n)
                    seen.add(n.part_no)

        candidates_meta = []
        for n in matched_nodes:
            successors = list(self.repo.graph.successors(n.part_no))
            candidates_meta.append({
                "part_no": n.part_no,
                "part_name": n.part_name,
                "lvl": n.lvl,
                "parent_part_no": n.parent_part_no,
                "supply_type": n.supply_type,
                "is_assembly": len(successors) > 0,
                "child_count": len(successors)
            })

        res = match_target_node_with_llm(candidates_meta, "용접 타입 Fan 적용", "Conv. Fan")
        self.assertIsNotNone(res.get("matched_part_no"))
        self.assertEqual(res.get("matched_part_no"), "5900W1N004D")
        self.assertGreaterEqual(res.get("score", 0), 80)
        print(f"\n[Test] Fan Scenario Match Result: {res}")


if __name__ == "__main__":
    unittest.main()
