import pytest

from sentry_sdk import capture_exception
from sentry_sdk.integrations.gnu_backtrace import GnuBacktraceIntegration

LINES = r"""
0. clickhouse-server(StackTrace::StackTrace()+0x16) [0x99d31a6]
1. clickhouse-server(DB::Exception::Exception(std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const&, int)+0x22) [0x372c822]
10. clickhouse-server(DB::ActionsVisitor::visit(std::shared_ptr<DB::IAST> const&)+0x1a12) [0x6ae45d2]
10. clickhouse-server(DB::InterpreterSelectQuery::executeImpl(DB::InterpreterSelectQuery::Pipeline&, std::shared_ptr<DB::IBlockInputStream> const&, bool)+0x11af) [0x75c68ff]
10. clickhouse-server(ThreadPoolImpl<std::thread>::worker(std::_List_iterator<std::thread>)+0x1ab) [0x6f90c1b]
11. clickhouse-server() [0xae06ddf]
11. clickhouse-server(DB::ExpressionAnalyzer::getRootActions(std::shared_ptr<DB::IAST> const&, bool, std::shared_ptr<DB::ExpressionActions>&, bool)+0xdb) [0x6a0a63b]
11. clickhouse-server(DB::InterpreterSelectQuery::InterpreterSelectQuery(std::shared_ptr<DB::IAST> const&, DB::Context const&, std::shared_ptr<DB::IBlockInputStream> const&, std::shared_ptr<DB::IStorage> const&, std::vector<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > > > const&, DB::QueryProcessingStage::Enum, unsigned long, bool)+0x5e6) [0x75c7516]
12. /lib/x86_64-linux-gnu/libpthread.so.0(+0x8184) [0x7f3bbc568184]
12. clickhouse-server(DB::ExpressionAnalyzer::getConstActions()+0xc9) [0x6a0b059]
12. clickhouse-server(DB::InterpreterSelectQuery::InterpreterSelectQuery(std::shared_ptr<DB::IAST> const&, DB::Context const&, std::vector<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > > > const&, DB::QueryProcessingStage::Enum, unsigned long, bool)+0x56) [0x75c8276]
13. /lib/x86_64-linux-gnu/libc.so.6(clone+0x6d) [0x7f3bbbb8303d]
13. clickhouse-server(DB::InterpreterSelectWithUnionQuery::InterpreterSelectWithUnionQuery(std::shared_ptr<DB::IAST> const&, DB::Context const&, std::vector<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > > > const&, DB::QueryProcessingStage::Enum, unsigned long, bool)+0x7e7) [0x75d4067]
13. clickhouse-server(DB::evaluateConstantExpression(std::shared_ptr<DB::IAST> const&, DB::Context const&)+0x3ed) [0x656bfdd]
14. clickhouse-server(DB::InterpreterFactory::get(std::shared_ptr<DB::IAST>&, DB::Context&, DB::QueryProcessingStage::Enum)+0x3a8) [0x75b0298]
14. clickhouse-server(DB::makeExplicitSet(DB::ASTFunction const*, DB::Block const&, bool, DB::Context const&, DB::SizeLimits const&, std::unordered_map<DB::PreparedSetKey, std::shared_ptr<DB::Set>, DB::PreparedSetKey::Hash, std::equal_to<DB::PreparedSetKey>, std::allocator<std::pair<DB::PreparedSetKey const, std::shared_ptr<DB::Set> > > >&)+0x382) [0x6adf692]
15. clickhouse-server() [0x7664c79]
15. clickhouse-server(DB::ActionsVisitor::makeSet(DB::ASTFunction const*, DB::Block const&)+0x2a7) [0x6ae2227]
16. clickhouse-server(DB::ActionsVisitor::visit(std::shared_ptr<DB::IAST> const&)+0x1973) [0x6ae4533]
16. clickhouse-server(DB::executeQuery(std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const&, DB::Context&, bool, DB::QueryProcessingStage::Enum)+0x8a) [0x76669fa]
17. clickhouse-server(DB::ActionsVisitor::visit(std::shared_ptr<DB::IAST> const&)+0x1324) [0x6ae3ee4]
17. clickhouse-server(DB::TCPHandler::runImpl()+0x4b9) [0x30973c9]
18. clickhouse-server(DB::ExpressionAnalyzer::getRootActions(std::shared_ptr<DB::IAST> const&, bool, std::shared_ptr<DB::ExpressionActions>&, bool)+0xdb) [0x6a0a63b]
18. clickhouse-server(DB::TCPHandler::run()+0x2b) [0x30985ab]
19. clickhouse-server(DB::ExpressionAnalyzer::appendGroupBy(DB::ExpressionActionsChain&, bool)+0x100) [0x6a0b4f0]
19. clickhouse-server(Poco::Net::TCPServerConnection::start()+0xf) [0x9b53e4f]
2. clickhouse-server(DB::FunctionTuple::getReturnTypeImpl(std::vector<std::shared_ptr<DB::IDataType const>, std::allocator<std::shared_ptr<DB::IDataType const> > > const&) const+0x122) [0x3a2a0f2]
2. clickhouse-server(DB::readException(DB::Exception&, DB::ReadBuffer&, std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const&)+0x21f) [0x6fb253f]
2. clickhouse-server(void DB::readDateTimeTextFallback<void>(long&, DB::ReadBuffer&, DateLUTImpl const&)+0x318) [0x99ffed8]
20. clickhouse-server(DB::InterpreterSelectQuery::analyzeExpressions(DB::QueryProcessingStage::Enum, bool)+0x364) [0x6437fa4]
20. clickhouse-server(Poco::Net::TCPServerDispatcher::run()+0x16a) [0x9b5422a]
21. clickhouse-server(DB::InterpreterSelectQuery::executeImpl(DB::InterpreterSelectQuery::Pipeline&, std::shared_ptr<DB::IBlockInputStream> const&, bool)+0x36d) [0x643c28d]
21. clickhouse-server(Poco::PooledThread::run()+0x77) [0x9c70f37]
22. clickhouse-server(DB::InterpreterSelectQuery::executeWithMultipleStreams()+0x50) [0x643ecd0]
22. clickhouse-server(Poco::ThreadImpl::runnableEntry(void*)+0x38) [0x9c6caa8]
23. clickhouse-server() [0xa3c68cf]
23. clickhouse-server(DB::InterpreterSelectWithUnionQuery::executeWithMultipleStreams()+0x6c) [0x644805c]
24. /lib/x86_64-linux-gnu/libpthread.so.0(+0x8184) [0x7fe839d2d184]
24. clickhouse-server(DB::InterpreterSelectWithUnionQuery::execute()+0x38) [0x6448658]
25. /lib/x86_64-linux-gnu/libc.so.6(clone+0x6d) [0x7fe83934803d]
25. clickhouse-server() [0x65744ef]
26. clickhouse-server(DB::executeQuery(std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const&, DB::Context&, bool, DB::QueryProcessingStage::Enum, bool)+0x81) [0x6576141]
27. clickhouse-server(DB::TCPHandler::runImpl()+0x752) [0x3739f82]
28. clickhouse-server(DB::TCPHandler::run()+0x2b) [0x373a5cb]
29. clickhouse-server(Poco::Net::TCPServerConnection::start()+0xf) [0x708e63f]
3. clickhouse-server(DB::Connection::receiveException()+0x81) [0x67d3ad1]
3. clickhouse-server(DB::DefaultFunctionBuilder::getReturnTypeImpl(std::vector<DB::ColumnWithTypeAndName, std::allocator<DB::ColumnWithTypeAndName> > const&) const+0x223) [0x38ac3b3]
3. clickhouse-server(DB::FunctionComparison<DB::NotEqualsOp, DB::NameNotEquals>::executeDateOrDateTimeOrEnumOrUUIDWithConstString(DB::Block&, unsigned long, DB::IColumn const*, DB::IColumn const*, std::shared_ptr<DB::IDataType const> const&, std::shared_ptr<DB::IDataType const> const&, bool, unsigned long)+0xbb3) [0x411dee3]
30. clickhouse-server(Poco::Net::TCPServerDispatcher::run()+0xe9) [0x708ed79]
31. clickhouse-server(Poco::PooledThread::run()+0x81) [0x7142011]
4. clickhouse-server(DB::Connection::receivePacket()+0x767) [0x67d9cd7]
4. clickhouse-server(DB::FunctionBuilderImpl::getReturnTypeWithoutLowCardinality(std::vector<DB::ColumnWithTypeAndName, std::allocator<DB::ColumnWithTypeAndName> > const&) const+0x75) [0x6869635]
4. clickhouse-server(DB::FunctionComparison<DB::NotEqualsOp, DB::NameNotEquals>::executeImpl(DB::Block&, std::vector<unsigned long, std::allocator<unsigned long> > const&, unsigned long, unsigned long)+0x576) [0x41ab006]
5. clickhouse-server(DB::FunctionBuilderImpl::getReturnType(std::vector<DB::ColumnWithTypeAndName, std::allocator<DB::ColumnWithTypeAndName> > const&) const+0x350) [0x6869f10]
5. clickhouse-server(DB::MultiplexedConnections::receivePacket()+0x7e) [0x67e7ede]
5. clickhouse-server(DB::PreparedFunctionImpl::execute(DB::Block&, std::vector<unsigned long, std::allocator<unsigned long> > const&, unsigned long, unsigned long)+0x3e2) [0x7933492]
6. clickhouse-server(DB::ExpressionAction::execute(DB::Block&, std::unordered_map<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, unsigned long, std::hash<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > >, std::equal_to<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > >, std::allocator<std::pair<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const, unsigned long> > >&) const+0x61a) [0x7ae093a]
6. clickhouse-server(DB::FunctionBuilderImpl::build(std::vector<DB::ColumnWithTypeAndName, std::allocator<DB::ColumnWithTypeAndName> > const&) const+0x3c) [0x38accfc]
6. clickhouse-server(DB::RemoteBlockInputStream::readImpl()+0x87) [0x631da97]
7. clickhouse-server(DB::ExpressionActions::addImpl(DB::ExpressionAction, std::vector<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > > >&)+0x552) [0x6a00052]
7. clickhouse-server(DB::ExpressionActions::execute(DB::Block&) const+0xe6) [0x7ae1e06]
7. clickhouse-server(DB::IBlockInputStream::read()+0x178) [0x63075e8]
8. clickhouse-server(DB::ExpressionActions::add(DB::ExpressionAction const&, std::vector<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > > >&)+0x42) [0x6a00422]
8. clickhouse-server(DB::FilterBlockInputStream::FilterBlockInputStream(std::shared_ptr<DB::IBlockInputStream> const&, std::shared_ptr<DB::ExpressionActions> const&, std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const&, bool)+0x711) [0x79970d1]
8. clickhouse-server(DB::ParallelInputsProcessor<DB::UnionBlockInputStream::Handler>::thread(std::shared_ptr<DB::ThreadGroupStatus>, unsigned long)+0x2f1) [0x64467c1]
9. clickhouse-server() [0x75bd5a3]
9. clickhouse-server(DB::ScopeStack::addAction(DB::ExpressionAction const&)+0xd2) [0x6ae04d2]
9. clickhouse-server(ThreadFromGlobalPool::ThreadFromGlobalPool<DB::ParallelInputsProcessor<DB::UnionBlockInputStream::Handler>::process()::{lambda()#1}>(DB::ParallelInputsProcessor<DB::UnionBlockInputStream::Handler>::process()::{lambda()#1}&&)::{lambda()#1}::operator()() const+0x6d) [0x644722d]
"""


@pytest.mark.parametrize("input", LINES.strip().splitlines())
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

    if frame.get("function") is None:
        assert "clickhouse-server()" in input or "pthread" in input
    else:
        assert ")" not in frame["function"] and "(" not in frame["function"]
        assert frame["function"] in input
