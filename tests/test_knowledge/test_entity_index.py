"""
MedicalEntityIndex 单元测试

覆盖：实体抽取、倒排索引构建与查询、IDF 加权、增量增删、边界
"""
import pytest
from mediZJ.knowledge.entity_index import MedicalEntityIndex, _MEDICAL_CODE_RE, _STOP_WORDS


class TestEntityExtraction:
    """实体抽取规则测试"""

    def test_chinese_medical_terms(self):
        idx = MedicalEntityIndex()
        entities = idx._extract_entities("高血压患者使用二甲双胍控制血糖")
        assert "高血压" in entities
        # jieba 将 "二甲双胍" 切分为 "二甲" + "双胍"
        assert "二甲" in entities
        assert "血糖" in entities
        assert "患者" not in entities  # 停用词

    def test_icd_codes(self):
        idx = MedicalEntityIndex()
        entities = idx._extract_entities("ICD编码 I10 和 E11.2 是常见编码")
        assert "I10" in entities
        assert "E11.2" in entities

    def test_english_abbreviations(self):
        idx = MedicalEntityIndex()
        entities = idx._extract_entities("使用 ACEI 和 CCB 类药物")
        assert "ACEI" in entities
        assert "CCB" in entities

    def test_drug_suffixes(self):
        idx = MedicalEntityIndex()
        # "Nifedipine" 后缀 "ine", "Atenolol" 后缀 "lol" 均在正则列表中
        entities = idx._extract_entities("Nifedipine 和 Atenolol 是常用降压药")
        assert "Nifedipine" in entities
        assert "Atenolol" in entities

    def test_dosage_units(self):
        idx = MedicalEntityIndex()
        entities = idx._extract_entities("血压目标 140mmHg 以下")
        assert "140mmHg" in entities

    def test_stop_words_filtered(self):
        idx = MedicalEntityIndex()
        entities = idx._extract_entities("患者需要进行药物治疗和症状管理")
        assert "患者" not in entities
        assert "治疗" not in entities
        assert "药物" not in entities
        assert "症状" not in entities

    def test_single_char_discarded(self):
        idx = MedicalEntityIndex()
        entities = idx._extract_entities("咳 痰 喘是常见症状")
        assert "咳" not in entities  # 单字丢弃
        assert "痰" not in entities
        assert "喘" not in entities

    def test_deduplication_preserves_order(self):
        idx = MedicalEntityIndex()
        entities = idx._extract_entities("高血压 高血压 糖尿病")
        assert entities == ["高血压", "糖尿病"]


class TestInvertedIndex:
    """倒排索引构建与查询测试"""

    @pytest.fixture
    def sample_docs(self):
        return [
            {"doc_id": "D1", "text": "高血压患者使用ACEI类药物降压治疗"},
            {"doc_id": "D2", "text": "高血压合并糖尿病的二甲双胍治疗方案"},
            {"doc_id": "D3", "text": "感冒发烧的常规处理方法"},
        ]

    def test_build_and_search_basic(self, sample_docs):
        idx = MedicalEntityIndex()
        idx.build_from_kb(sample_docs)
        result = idx.search("高血压")
        assert "D1" in result
        assert "D2" in result

    def test_multi_entity_cumulative(self, sample_docs):
        idx = MedicalEntityIndex()
        idx.build_from_kb(sample_docs)
        result = idx.search("高血压 糖尿病")
        # D2 命中两个实体，D1 命中一个
        assert result["D2"] > result["D1"]

    def test_no_match_returns_empty(self, sample_docs):
        idx = MedicalEntityIndex()
        idx.build_from_kb(sample_docs)
        result = idx.search("骨折")
        assert result == {}

    def test_scores_normalized_to_one(self, sample_docs):
        idx = MedicalEntityIndex()
        idx.build_from_kb(sample_docs)
        result = idx.search("高血压 糖尿病 二甲双胍")
        max_score = max(result.values()) if result else 0
        assert max_score == pytest.approx(1.0)

    def test_stopword_only_query(self, sample_docs):
        idx = MedicalEntityIndex()
        idx.build_from_kb(sample_docs)
        result = idx.search("患者需要治疗")
        assert result == {}


class TestIdfWeighting:
    """IDF 加权测试"""

    @pytest.fixture
    def skewed_docs(self):
        return [
            {"doc_id": "D_common", "text": "高血压患者日常注意事项"},
            {"doc_id": "D_rare", "text": "冠心病患者日常注意事项"},
            {"doc_id": "D2", "text": "高血压合并冠心病的综合治疗"},
        ]

    def test_rare_entity_weights_higher(self, skewed_docs):
        idx = MedicalEntityIndex()
        idx.build_from_kb(skewed_docs)

        result = idx.search("高血压 冠心病")

        # "高血压" 和 "冠心病" 各出现在 2 篇文档 → IDF 相同
        # D_common 只命中 "高血压"，D_rare 只命中 "冠心病" → 分数相等
        assert result["D_common"] == pytest.approx(result["D_rare"])
        # D2 命中两者 → 最高分 (1.0)
        assert result["D2"] == pytest.approx(1.0)
        assert result["D2"] > result["D_common"]


class TestIncrementalUpdate:
    """增量增删一致性测试"""

    def test_add_document(self):
        idx = MedicalEntityIndex()
        idx.add_document("D1", "高血压患者使用ACEI类药物")
        idx.add_document("D2", "高血压合并糖尿病")

        result = idx.search("高血压")
        assert "D1" in result
        assert "D2" in result
        assert idx._doc_count == 2

    def test_remove_document(self):
        idx = MedicalEntityIndex()
        idx.add_document("D1", "高血压患者使用ACEI")
        idx.add_document("D2", "糖尿病治疗")

        idx.remove_document("D1")

        result = idx.search("高血压")
        assert "D1" not in result
        assert idx._doc_count == 1
        # entity_to_docs 中不应该有空集合
        assert all(v for v in idx.entity_to_docs.values())

    def test_remove_updates_df(self):
        idx = MedicalEntityIndex()
        idx.add_document("D1", "高血压 罕见病X")
        idx.add_document("D2", "高血压")

        # 删除后 "罕见病X" 应完全从索引中消失
        idx.remove_document("D1")
        assert "罕见病X" not in idx.entity_to_docs
        assert "罕见病X" not in idx._entity_df

    def test_full_cycle(self):
        """build → add → remove → search 完整链路"""
        idx = MedicalEntityIndex()
        idx.add_document("D1", "高血压 ACEI")
        idx.add_document("D2", "糖尿病 二甲双胍")

        assert idx._doc_count == 2

        idx.remove_document("D1")
        result = idx.search("ACEI")
        assert result == {}

        idx.add_document("D3", "冠心病 ACEI")
        result = idx.search("ACEI")
        assert "D3" in result


class TestEdgeCases:
    """边界条件测试"""

    def test_empty_document(self):
        idx = MedicalEntityIndex()
        idx.build_from_kb([])
        assert idx._doc_count == 0
        assert idx.search("高血压") == {}

    def test_pure_stopword_doc(self):
        idx = MedicalEntityIndex()
        idx.add_document("D1", "患者需要治疗进行管理")
        # 纯停用词文档不产生任何实体
        result = idx.search("治疗")
        assert result == {}

    def test_empty_query(self):
        idx = MedicalEntityIndex()
        idx.add_document("D1", "高血压患者")
        assert idx.search("") == {}

    def test_nonexistent_doc_removal(self):
        idx = MedicalEntityIndex()
        idx.add_document("D1", "高血压")
        # 删除不存在的文档不应崩溃
        idx.remove_document("D_nonexistent")
        assert idx._doc_count == 0
        assert "D1" in idx.entity_to_docs.get("高血压", set())


class TestMedicalCodeRegex:
    """医学编码正则测试"""

    def test_icd_code_positive(self):
        assert _MEDICAL_CODE_RE.match("I10")
        assert _MEDICAL_CODE_RE.match("E11.2")
        assert _MEDICAL_CODE_RE.match("A15")

    def test_abbreviation_positive(self):
        assert _MEDICAL_CODE_RE.match("ACEI")
        assert _MEDICAL_CODE_RE.match("BMI")
        assert _MEDICAL_CODE_RE.match("CT")

    def test_drug_name_positive(self):
        # "Nifedipine" → 后缀 "ine", "Lisinopril" → 后缀 "pril", "Amlodipine" → 后缀 "pine"
        assert _MEDICAL_CODE_RE.match("Nifedipine")
        assert _MEDICAL_CODE_RE.match("Lisinopril")
        assert _MEDICAL_CODE_RE.match("Amlodipine")

    def test_non_medical_rejected(self):
        assert not _MEDICAL_CODE_RE.match("hello")
        assert not _MEDICAL_CODE_RE.match("abc")  # 不是大写缩写
        assert not _MEDICAL_CODE_RE.match("123")


class TestStopWords:
    """停用词表测试"""

    def test_common_stop_words_present(self):
        assert "患者" in _STOP_WORDS
        assert "治疗" in _STOP_WORDS
        assert "症状" in _STOP_WORDS
        assert "检查" in _STOP_WORDS
        assert "诊断" in _STOP_WORDS

    def test_medical_terms_not_in_stop_words(self):
        assert "高血压" not in _STOP_WORDS
        assert "糖尿病" not in _STOP_WORDS
        assert "ACEI" not in _STOP_WORDS
