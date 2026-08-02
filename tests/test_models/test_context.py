from dan.models.context import (
    ArtifactRef,
    CompactionRule,
    CompactionStrategy,
    ContextDeclaration,
    ContextMode,
    ContextProjection,
    FailurePolicy,
    MergeStrategy,
    NodeLocalState,
    SharedContextDeclaration,
)


class TestContextDeclaration:
    def test_read(self):
        d = ContextDeclaration(key="outline", mode=ContextMode.READ)
        assert d.mode == ContextMode.READ
        assert d.json_schema is None

    def test_write_with_schema(self):
        d = ContextDeclaration(
            key="draft",
            mode=ContextMode.WRITE,
            json_schema={"type": "string"},
        )
        assert d.mode == ContextMode.WRITE


class TestCompactionRule:
    def test_sliding_window(self):
        r = CompactionRule(strategy=CompactionStrategy.SLIDING_WINDOW, window_size=5)
        assert r.window_size == 5

    def test_defaults(self):
        r = CompactionRule()
        assert r.strategy == CompactionStrategy.NONE


class TestFailurePolicy:
    def test_all_fields(self):
        fp = FailurePolicy(max_iterations=10, timeout_seconds=60.0, stagnation_threshold=3)
        assert fp.max_iterations == 10


class TestNodeLocalState:
    def test_empty(self):
        s = NodeLocalState()
        assert s.json_schema == {}

    def test_with_schema(self):
        s = NodeLocalState(json_schema={"type": "object", "properties": {"count": {"type": "integer"}}})
        assert "count" in s.json_schema["properties"]


class TestSharedContextDeclaration:
    def test_round_trip(self):
        d = SharedContextDeclaration(key="bibliography", json_schema={"type": "array"}, description="refs")
        assert SharedContextDeclaration.model_validate(d.model_dump()) == d


class TestArtifactRef:
    def test_minimal(self):
        a = ArtifactRef(uri="artifact://draft/v1")
        assert a.media_type == "application/octet-stream"

    def test_with_hash(self):
        a = ArtifactRef(uri="artifact://fig1.png", content_hash="sha256:abc123", media_type="image/png")
        assert a.content_hash == "sha256:abc123"


class TestContextProjection:
    def test_construction(self):
        p = ContextProjection(
            name="reviser_view",
            context_keys=["draft", "comments"],
            local_state_keys=["iteration_count"],
        )
        assert len(p.context_keys) == 2


class TestMergeStrategy:
    def test_values(self):
        assert MergeStrategy.APPEND == "append"
        assert MergeStrategy.LAST_WRITE_WINS == "last_write_wins"
        assert MergeStrategy.REDUCER == "reducer"
