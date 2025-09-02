import pytest

from sentry_sdk import capture_exception
from sentry_sdk.integrations.gnu_backtrace import GnuBacktraceIntegration

LINES = r"""
0. DB::Exception::Exception(DB::Exception::MessageMasked&&, int, bool) @ 0x000000000bfc38a4 in /usr/bin/clickhouse
1. DB::Exception::Exception<String, String>(int, FormatStringHelperImpl<std::type_identity<String>::type, std::type_identity<String>::type>, String&&, String&&) @ 0x00000000075d242c in /usr/bin/clickhouse
2. DB::ActionsMatcher::visit(DB::ASTIdentifier const&, std::shared_ptr<DB::IAST> const&, DB::ActionsMatcher::Data&) @ 0x0000000010b1c648 in /usr/bin/clickhouse
3. DB::ActionsMatcher::visit(DB::ASTFunction const&, std::shared_ptr<DB::IAST> const&, DB::ActionsMatcher::Data&) @ 0x0000000010b1f58c in /usr/bin/clickhouse
4. DB::ActionsMatcher::visit(DB::ASTFunction const&, std::shared_ptr<DB::IAST> const&, DB::ActionsMatcher::Data&) @ 0x0000000010b1f58c in /usr/bin/clickhouse
5. DB::ActionsMatcher::visit(std::shared_ptr<DB::IAST> const&, DB::ActionsMatcher::Data&) @ 0x0000000010b1c394 in /usr/bin/clickhouse
6. DB::InDepthNodeVisitor<DB::ActionsMatcher, true, false, std::shared_ptr<DB::IAST> const>::doVisit(std::shared_ptr<DB::IAST> const&) @ 0x0000000010b154a0 in /usr/bin/clickhouse
7. DB::ExpressionAnalyzer::getRootActions(std::shared_ptr<DB::IAST> const&, bool, std::shared_ptr<DB::ActionsDAG>&, bool) @ 0x0000000010af83b4 in /usr/bin/clickhouse
8. DB::SelectQueryExpressionAnalyzer::appendSelect(DB::ExpressionActionsChain&, bool) @ 0x0000000010aff168 in /usr/bin/clickhouse
9. DB::ExpressionAnalysisResult::ExpressionAnalysisResult(DB::SelectQueryExpressionAnalyzer&, std::shared_ptr<DB::StorageInMemoryMetadata const> const&, bool, bool, bool, std::shared_ptr<DB::FilterDAGInfo> const&, std::shared_ptr<DB::FilterDAGInfo> const&, DB::Block const&) @ 0x0000000010b05b74 in /usr/bin/clickhouse
10. DB::InterpreterSelectQuery::getSampleBlockImpl() @ 0x00000000111559fc in /usr/bin/clickhouse
11. DB::InterpreterSelectQuery::InterpreterSelectQuery(std::shared_ptr<DB::IAST> const&, std::shared_ptr<DB::Context> const&, std::optional<DB::Pipe>, std::shared_ptr<DB::IStorage> const&, DB::SelectQueryOptions const&, std::vector<String, std::allocator<String>> const&, std::shared_ptr<DB::StorageInMemoryMetadata const> const&, std::shared_ptr<DB::PreparedSets>)::$_0::operator()(bool) const @ 0x0000000011148254 in /usr/bin/clickhouse
12. DB::InterpreterSelectQuery::InterpreterSelectQuery(std::shared_ptr<DB::IAST> const&, std::shared_ptr<DB::Context> const&, std::optional<DB::Pipe>, std::shared_ptr<DB::IStorage> const&, DB::SelectQueryOptions const&, std::vector<String, std::allocator<String>> const&, std::shared_ptr<DB::StorageInMemoryMetadata const> const&, std::shared_ptr<DB::PreparedSets>) @ 0x00000000111413e8 in /usr/bin/clickhouse
13. DB::InterpreterSelectWithUnionQuery::InterpreterSelectWithUnionQuery(std::shared_ptr<DB::IAST> const&, std::shared_ptr<DB::Context>, DB::SelectQueryOptions const&, std::vector<String, std::allocator<String>> const&) @ 0x00000000111d3708 in /usr/bin/clickhouse
14. DB::InterpreterFactory::get(std::shared_ptr<DB::IAST>&, std::shared_ptr<DB::Context>, DB::SelectQueryOptions const&) @ 0x0000000011100b64 in /usr/bin/clickhouse
15. DB::executeQueryImpl(char const*, char const*, std::shared_ptr<DB::Context>, bool, DB::QueryProcessingStage::Enum, DB::ReadBuffer*) @ 0x00000000114c3f3c in /usr/bin/clickhouse
16. DB::executeQuery(String const&, std::shared_ptr<DB::Context>, bool, DB::QueryProcessingStage::Enum) @ 0x00000000114c0ec8 in /usr/bin/clickhouse
17. DB::TCPHandler::runImpl() @ 0x00000000121bb5d8 in /usr/bin/clickhouse
18. DB::TCPHandler::run() @ 0x00000000121cb728 in /usr/bin/clickhouse
19. Poco::Net::TCPServerConnection::start() @ 0x00000000146d9404 in /usr/bin/clickhouse
20. Poco::Net::TCPServerDispatcher::run() @ 0x00000000146da900 in /usr/bin/clickhouse
21. Poco::PooledThread::run() @ 0x000000001484da7c in /usr/bin/clickhouse
22. Poco::ThreadImpl::runnableEntry(void*) @ 0x000000001484bc24 in /usr/bin/clickhouse
23. start_thread @ 0x0000000000007624 in /usr/lib/aarch64-linux-gnu/libpthread-2.31.so
24. ? @ 0x00000000000d162c in /usr/lib/aarch64-linux-gnu/libc-2.31.so
"""

LINES_NO_PATH = r"""
0. DB::Exception::Exception(DB::Exception::MessageMasked&&, int, bool) @ 0x000000000bfc38a4
1. DB::Exception::Exception<String, String>(int, FormatStringHelperImpl<std::type_identity<String>::type, std::type_identity<String>::type>, String&&, String&&) @ 0x00000000075d242c
2. DB::ActionsMatcher::visit(DB::ASTIdentifier const&, std::shared_ptr<DB::IAST> const&, DB::ActionsMatcher::Data&) @ 0x0000000010b1c648
3. DB::ActionsMatcher::visit(DB::ASTFunction const&, std::shared_ptr<DB::IAST> const&, DB::ActionsMatcher::Data&) @ 0x0000000010b1f58c
4. DB::ActionsMatcher::visit(DB::ASTFunction const&, std::shared_ptr<DB::IAST> const&, DB::ActionsMatcher::Data&) @ 0x0000000010b1f58c
5. DB::ActionsMatcher::visit(std::shared_ptr<DB::IAST> const&, DB::ActionsMatcher::Data&) @ 0x0000000010b1c394
6. DB::InDepthNodeVisitor<DB::ActionsMatcher, true, false, std::shared_ptr<DB::IAST> const>::doVisit(std::shared_ptr<DB::IAST> const&) @ 0x0000000010b154a0
7. DB::ExpressionAnalyzer::getRootActions(std::shared_ptr<DB::IAST> const&, bool, std::shared_ptr<DB::ActionsDAG>&, bool) @ 0x0000000010af83b4
8. DB::SelectQueryExpressionAnalyzer::appendSelect(DB::ExpressionActionsChain&, bool) @ 0x0000000010aff168
9. DB::ExpressionAnalysisResult::ExpressionAnalysisResult(DB::SelectQueryExpressionAnalyzer&, std::shared_ptr<DB::StorageInMemoryMetadata const> const&, bool, bool, bool, std::shared_ptr<DB::FilterDAGInfo> const&, std::shared_ptr<DB::FilterDAGInfo> const&, DB::Block const&) @ 0x0000000010b05b74
10. DB::InterpreterSelectQuery::getSampleBlockImpl() @ 0x00000000111559fc
11. DB::InterpreterSelectQuery::InterpreterSelectQuery(std::shared_ptr<DB::IAST> const&, std::shared_ptr<DB::Context> const&, std::optional<DB::Pipe>, std::shared_ptr<DB::IStorage> const&, DB::SelectQueryOptions const&, std::vector<String, std::allocator<String>> const&, std::shared_ptr<DB::StorageInMemoryMetadata const> const&, std::shared_ptr<DB::PreparedSets>)::$_0::operator()(bool) const @ 0x0000000011148254
12. DB::InterpreterSelectQuery::InterpreterSelectQuery(std::shared_ptr<DB::IAST> const&, std::shared_ptr<DB::Context> const&, std::optional<DB::Pipe>, std::shared_ptr<DB::IStorage> const&, DB::SelectQueryOptions const&, std::vector<String, std::allocator<String>> const&, std::shared_ptr<DB::StorageInMemoryMetadata const> const&, std::shared_ptr<DB::PreparedSets>) @ 0x00000000111413e8
13. DB::InterpreterSelectWithUnionQuery::InterpreterSelectWithUnionQuery(std::shared_ptr<DB::IAST> const&, std::shared_ptr<DB::Context>, DB::SelectQueryOptions const&, std::vector<String, std::allocator<String>> const&) @ 0x00000000111d3708
14. DB::InterpreterFactory::get(std::shared_ptr<DB::IAST>&, std::shared_ptr<DB::Context>, DB::SelectQueryOptions const&) @ 0x0000000011100b64
15. DB::executeQueryImpl(char const*, char const*, std::shared_ptr<DB::Context>, bool, DB::QueryProcessingStage::Enum, DB::ReadBuffer*) @ 0x00000000114c3f3c
16. DB::executeQuery(String const&, std::shared_ptr<DB::Context>, bool, DB::QueryProcessingStage::Enum) @ 0x00000000114c0ec8
17. DB::TCPHandler::runImpl() @ 0x00000000121bb5d8
18. DB::TCPHandler::run() @ 0x00000000121cb728
19. Poco::Net::TCPServerConnection::start() @ 0x00000000146d9404
20. Poco::Net::TCPServerDispatcher::run() @ 0x00000000146da900
21. Poco::PooledThread::run() @ 0x000000001484da7c
22. Poco::ThreadImpl::runnableEntry(void*) @ 0x000000001484bc24
23. start_thread @ 0x0000000000007624
24. ? @ 0x00000000000d162c
"""


@pytest.mark.parametrize(
    "input", LINES.strip().splitlines() + LINES_NO_PATH.strip().splitlines()
)
def test_basic(sentry_init, capture_events, input):
    sentry_init(integrations=[GnuBacktraceIntegration()])
    events = capture_events()

    try:
        raise ValueError(input)
    except ValueError:
        capture_exception()

    (event,) = events
    (exception,) = event["exception"]["values"]

    assert (
        exception["value"]
        == "<stacktrace parsed and removed by GnuBacktraceIntegration>"
    )
    (frame,) = exception["stacktrace"]["frames"][1:]

    assert frame["function"]
