from sentry_sdk import capture_exception
from sentry_sdk.integrations.gnu_backtrace import GnuBacktraceIntegration

EXC_VALUE = r"""
DB::Exception: Cannot parse datetime. Stack trace:

0. clickhouse-server(StackTrace::StackTrace()+0x16) [0x99d31a6]
1. clickhouse-server(DB::Exception::Exception(std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const&, int)+0x22) [0x3089bb2]
2. clickhouse-server(void DB::readDateTimeTextFallback<void>(long&, DB::ReadBuffer&, DateLUTImpl const&)+0x318) [0x99ffed8]
3. clickhouse-server(DB::FunctionComparison<DB::NotEqualsOp, DB::NameNotEquals>::executeDateOrDateTimeOrEnumOrUUIDWithConstString(DB::Block&, unsigned long, DB::IColumn const*, DB::IColumn const*, std::shared_ptr<DB::IDataType const> const&, std::shared_ptr<DB::IDataType const> const&, bool, unsigned long)+0xbb3) [0x411dee3]
4. clickhouse-server(DB::FunctionComparison<DB::NotEqualsOp, DB::NameNotEquals>::executeImpl(DB::Block&, std::vector<unsigned long, std::allocator<unsigned long> > const&, unsigned long, unsigned long)+0x576) [0x41ab006]
5. clickhouse-server(DB::PreparedFunctionImpl::execute(DB::Block&, std::vector<unsigned long, std::allocator<unsigned long> > const&, unsigned long, unsigned long)+0x3e2) [0x7933492]
6. clickhouse-server(DB::ExpressionAction::execute(DB::Block&, std::unordered_map<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, unsigned long, std::hash<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > >, std::equal_to<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > >, std::allocator<std::pair<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const, unsigned long> > >&) const+0x61a) [0x7ae093a]
7. clickhouse-server(DB::ExpressionActions::execute(DB::Block&) const+0xe6) [0x7ae1e06]
8. clickhouse-server(DB::FilterBlockInputStream::FilterBlockInputStream(std::shared_ptr<DB::IBlockInputStream> const&, std::shared_ptr<DB::ExpressionActions> const&, std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const&, bool)+0x711) [0x79970d1]
9. clickhouse-server() [0x75bd5a3]
10. clickhouse-server(DB::InterpreterSelectQuery::executeImpl(DB::InterpreterSelectQuery::Pipeline&, std::shared_ptr<DB::IBlockInputStream> const&, bool)+0x11af) [0x75c68ff]
11. clickhouse-server(DB::InterpreterSelectQuery::InterpreterSelectQuery(std::shared_ptr<DB::IAST> const&, DB::Context const&, std::shared_ptr<DB::IBlockInputStream> const&, std::shared_ptr<DB::IStorage> const&, std::vector<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > > > const&, DB::QueryProcessingStage::Enum, unsigned long, bool)+0x5e6) [0x75c7516]
12. clickhouse-server(DB::InterpreterSelectQuery::InterpreterSelectQuery(std::shared_ptr<DB::IAST> const&, DB::Context const&, std::vector<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > > > const&, DB::QueryProcessingStage::Enum, unsigned long, bool)+0x56) [0x75c8276]
13. clickhouse-server(DB::InterpreterSelectWithUnionQuery::InterpreterSelectWithUnionQuery(std::shared_ptr<DB::IAST> const&, DB::Context const&, std::vector<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> >, std::allocator<std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > > > const&, DB::QueryProcessingStage::Enum, unsigned long, bool)+0x7e7) [0x75d4067]
14. clickhouse-server(DB::InterpreterFactory::get(std::shared_ptr<DB::IAST>&, DB::Context&, DB::QueryProcessingStage::Enum)+0x3a8) [0x75b0298]
15. clickhouse-server() [0x7664c79]
16. clickhouse-server(DB::executeQuery(std::__cxx11::basic_string<char, std::char_traits<char>, std::allocator<char> > const&, DB::Context&, bool, DB::QueryProcessingStage::Enum)+0x8a) [0x76669fa]
17. clickhouse-server(DB::TCPHandler::runImpl()+0x4b9) [0x30973c9]
18. clickhouse-server(DB::TCPHandler::run()+0x2b) [0x30985ab]
19. clickhouse-server(Poco::Net::TCPServerConnection::start()+0xf) [0x9b53e4f]
20. clickhouse-server(Poco::Net::TCPServerDispatcher::run()+0x16a) [0x9b5422a]
21. clickhouse-server(Poco::PooledThread::run()+0x77) [0x9c70f37]
22. clickhouse-server(Poco::ThreadImpl::runnableEntry(void*)+0x38) [0x9c6caa8]
23. clickhouse-server() [0xa3c68cf]
24. /lib/x86_64-linux-gnu/libpthread.so.0(+0x8184) [0x7fe839d2d184]
25. /lib/x86_64-linux-gnu/libc.so.6(clone+0x6d) [0x7fe83934803d]
"""


def test_basic(sentry_init, capture_events):
    sentry_init(integrations=[GnuBacktraceIntegration()])
    events = capture_events()

    try:
        raise ValueError(EXC_VALUE)
    except ValueError:
        capture_exception()

    event, = events
    exception, = event["exception"]["values"]

    assert exception["value"] == (
        "\n"
        "DB::Exception: Cannot parse datetime. Stack trace:\n"
        "\n"
        "<stacktrace parsed and removed by GnuBacktraceIntegration>"
    )

    assert exception["stacktrace"]["frames"][1:] == [
        {
            "function": "clone",
            "in_app": True,
            "package": "/lib/x86_64-linux-gnu/libc.so.6",
        },
        {"in_app": True, "package": "/lib/x86_64-linux-gnu/libpthread.so.0"},
        {"in_app": True, "package": "clickhouse-server"},
        {
            "function": "Poco::ThreadImpl::runnableEntry",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "Poco::PooledThread::run",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "Poco::Net::TCPServerDispatcher::run",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "Poco::Net::TCPServerConnection::start",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::TCPHandler::run",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::TCPHandler::runImpl",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::executeQuery",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {"in_app": True, "package": "clickhouse-server"},
        {
            "function": "DB::InterpreterFactory::get",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::InterpreterSelectWithUnionQuery::InterpreterSelectWithUnionQuery",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::InterpreterSelectQuery::InterpreterSelectQuery",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::InterpreterSelectQuery::InterpreterSelectQuery",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::InterpreterSelectQuery::executeImpl",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {"in_app": True, "package": "clickhouse-server"},
        {
            "function": "DB::FilterBlockInputStream::FilterBlockInputStream",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::ExpressionActions::execute",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::ExpressionAction::execute",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::PreparedFunctionImpl::execute",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::NameNotEquals>::executeImpl",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::NameNotEquals>::executeDateOrDateTimeOrEnumOrUUIDWithConstString",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::readDateTimeTextFallback<void>",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "DB::Exception::Exception",
            "in_app": True,
            "package": "clickhouse-server",
        },
        {
            "function": "StackTrace::StackTrace",
            "in_app": True,
            "package": "clickhouse-server",
        },
    ]
