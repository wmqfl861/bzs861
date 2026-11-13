cmake_minimum_required(VERSION 3.25)
project(bzs861 VERSION 0.1.10 LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
option(BZS_SANITIZERS "Enable address and undefined-behavior sanitizers" OFF)
option(BZS_BUILD_TESTS "Build offline tests" ON)
option(BZS_REQUIRE_PYTHON_TESTS "Fail configure if worker test dependencies are missing" OFF)
find_package(Threads REQUIRED)
find_package(SQLite3 3.38 REQUIRED)
find_package(LibXml2 REQUIRED)
find_package(OpenSSL REQUIRED COMPONENTS Crypto)
find_path(JSONC_INCLUDE_DIR json-c/json.h REQUIRED)
find_library(JSONC_LIBRARY NAMES json-c json-c-static REQUIRED)
find_path(BOOST_INCLUDE_DIR boost/beast.hpp REQUIRED)
add_library(bzs_core
  src/json.cpp src/util.cpp src/diagnostics.cpp src/query.cpp src/html.cpp src/tokenizer.cpp src/highlight.cpp
  src/store.cpp src/maintenance.cpp src/revocation.cpp src/sessions.cpp src/publication.cpp src/source_publication.cpp src/search_modes.cpp src/retention.cpp src/batch.cpp src/map.cpp src/crawl.cpp src/crawl_trap.cpp src/sites.cpp src/map_service.cpp src/contracts.cpp src/http_cache.cpp src/web.cpp src/service.cpp)
target_include_directories(bzs_core PUBLIC include PRIVATE ${JSONC_INCLUDE_DIR})
target_link_libraries(bzs_core PUBLIC SQLite::SQLite3 OpenSSL::Crypto PRIVATE LibXml2::LibXml2 ${JSONC_LIBRARY})
target_compile_definitions(bzs_core PUBLIC BZS_VERSION="${PROJECT_VERSION}")
if(MSVC)
  target_compile_options(bzs_core PRIVATE /W4 /permissive- /utf-8)
else()
  target_compile_options(bzs_core PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow)
endif()
if(BZS_SANITIZERS AND NOT MSVC)
  target_compile_options(bzs_core PUBLIC -fsanitize=address,undefined -fno-omit-frame-pointer)
  target_link_options(bzs_core PUBLIC -fsanitize=address,undefined)
endif()
foreach(app searchd searchctl)
  add_executable(${app} apps/${app}/main.cpp)
  target_include_directories(${app} PRIVATE ${BOOST_INCLUDE_DIR})
  target_compile_definitions(${app} PRIVATE BOOST_ERROR_CODE_HEADER_ONLY BOOST_SYSTEM_NO_LIB)
  target_link_libraries(${app} PRIVATE bzs_core Threads::Threads)
  if(WIN32)
    target_link_libraries(${app} PRIVATE ws2_32 mswsock)
    target_compile_definitions(${app} PRIVATE _WIN32_WINNT=0x0A00 NOMINMAX)
  endif()
  if(MSVC)
    target_compile_options(${app} PRIVATE /W4 /utf-8)
  else()
    target_compile_options(${app} PRIVATE -Wall -Wextra -Wpedantic)
  endif()
endforeach()
include(CTest)
# CL-11: single registration point for offline native test executables so
# every suite builds with the same warning set instead of per-target copies.
# bzs_add_native_test(<target> <source> [TEST <ctest-name>] [FIXTURES]
#                     [TIMEOUT <seconds>] [NO_TEST] [<command-args>...])
# Creates the executable, applies the uniform warnings (/W4 /utf-8 on MSVC,
# -Wall -Wextra -Wpedantic -Wconversion -Wshadow on GCC/Clang), defines
# BZS_FIXTURE_DIR when FIXTURES is given, and registers a ctest entry named
# TEST (default: the target name) with an optional TIMEOUT watchdog; extra
# arguments become test command arguments. NO_TEST builds a helper
# executable without a ctest entry (e.g. bzs_index_cost). Link libraries
# stay explicit at each call site, preserving the previous link semantics.
function(bzs_add_native_test name source)
  cmake_parse_arguments(BZS_TEST "FIXTURES;NO_TEST" "TEST;TIMEOUT" "" ${ARGN})
  add_executable(${name} ${source})
  if(MSVC)
    target_compile_options(${name} PRIVATE /W4 /utf-8)
  else()
    target_compile_options(${name} PRIVATE -Wall -Wextra -Wpedantic -Wconversion -Wshadow)
  endif()
  if(BZS_TEST_FIXTURES)
    target_compile_definitions(${name} PRIVATE BZS_FIXTURE_DIR="${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures")
  endif()
  if(NOT BZS_TEST_NO_TEST)
    if(BZS_TEST_TEST)
      set(BZS_TEST_NAME ${BZS_TEST_TEST})
    else()
      set(BZS_TEST_NAME ${name})
    endif()
    add_test(NAME ${BZS_TEST_NAME} COMMAND ${name} ${BZS_TEST_UNPARSED_ARGUMENTS})
    if(BZS_TEST_TIMEOUT)
      set_tests_properties(${BZS_TEST_NAME} PROPERTIES TIMEOUT ${BZS_TEST_TIMEOUT})
    endif()
  endif()
endfunction()
if(BZS_BUILD_TESTS)
  bzs_add_native_test(bzs_storage_maintenance_tests tests/storage_maintenance.cpp TEST storage_maintenance TIMEOUT 90)
  target_link_libraries(bzs_storage_maintenance_tests PRIVATE bzs_core)
  bzs_add_native_test(bzs_incremental_storage_tests tests/incremental_storage.cpp TEST incremental_storage TIMEOUT 30)
  target_link_libraries(bzs_incremental_storage_tests PRIVATE bzs_core)
  bzs_add_native_test(bzs_archive_quota_tests tests/archive_quota.cpp TEST archive_quota TIMEOUT 30)
  target_link_libraries(bzs_archive_quota_tests PRIVATE bzs_core)
  bzs_add_native_test(bzs_receipt_retention_tests tests/receipt_retention.cpp TEST receipt_retention TIMEOUT 20)
  target_link_libraries(bzs_receipt_retention_tests PRIVATE bzs_core)
  bzs_add_native_test(bzs_background_pool_tests tests/background_pool.cpp TEST background_pool TIMEOUT 60 FIXTURES)
  target_link_libraries(bzs_background_pool_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_gram_index_tests tests/gram_index.cpp TEST gram_index TIMEOUT 60)
  target_link_libraries(bzs_gram_index_tests PRIVATE bzs_core)
  bzs_add_native_test(bzs_index_cost tests/index_cost.cpp NO_TEST)
  target_link_libraries(bzs_index_cost PRIVATE bzs_core)
  bzs_add_native_test(bzs_long_document_recall_tests tests/long_document_recall.cpp TEST long_document_recall TIMEOUT 300 FIXTURES)
  target_link_libraries(bzs_long_document_recall_tests PRIVATE bzs_core)
  set_tests_properties(long_document_recall PROPERTIES LABELS "slow;search")
  bzs_add_native_test(bzs_request_executor_tests tests/request_executor.cpp TEST request_executor TIMEOUT 10)
  target_include_directories(bzs_request_executor_tests PRIVATE include)
  target_link_libraries(bzs_request_executor_tests PRIVATE Threads::Threads)
  bzs_add_native_test(bzs_tests tests/unit.cpp TEST core TIMEOUT 45 FIXTURES)
  target_link_libraries(bzs_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_batch_tests tests/batch.cpp TEST batch TIMEOUT 45 FIXTURES)
  target_link_libraries(bzs_batch_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_map_tests tests/map.cpp TEST map TIMEOUT 45 FIXTURES)
  target_link_libraries(bzs_map_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_crawl_tests tests/crawl.cpp TEST crawl TIMEOUT 45 FIXTURES)
  target_link_libraries(bzs_crawl_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_site_publication_tests tests/site_publication.cpp TEST site_publication TIMEOUT 60 FIXTURES)
  target_link_libraries(bzs_site_publication_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_retention_tests tests/retention.cpp TEST retention TIMEOUT 60 FIXTURES)
  target_link_libraries(bzs_retention_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_fairness_tests tests/fairness.cpp TEST fairness FIXTURES)
  target_link_libraries(bzs_fairness_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_session_tests tests/sessions.cpp TEST sessions)
  target_link_libraries(bzs_session_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_source_publication_tests tests/source_publication.cpp TEST source_publication TIMEOUT 60)
  target_link_libraries(bzs_source_publication_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_search_mode_tests tests/search_modes.cpp TEST search_modes TIMEOUT 60 FIXTURES)
  target_link_libraries(bzs_search_mode_tests PRIVATE bzs_core Threads::Threads)
  # CL-10 (2026-11-13 measurement round): Windows CI runs the heavy persistent
  # suites at RelWithDebInfo, where sessions takes 33.1 s median on the
  # reference Windows 11 VM (Debug: 245.7 s), so the watchdog returns to the
  # Linux value (60 s, 1.8x headroom) on every platform.
  set_tests_properties(sessions PROPERTIES TIMEOUT 60)
  bzs_add_native_test(bzs_revalidation_tests tests/revalidation.cpp TEST revalidation TIMEOUT 60)
  target_link_libraries(bzs_revalidation_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_direct_shared_tests tests/direct_shared.cpp TEST direct_shared TIMEOUT 90)
  target_link_libraries(bzs_direct_shared_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_revocation_tests tests/revocation.cpp TEST revocation TIMEOUT 60 FIXTURES)
  target_link_libraries(bzs_revocation_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_capacity_cycles_tests tests/capacity_cycles.cpp TEST capacity_cycles TIMEOUT 180)
  target_link_libraries(bzs_capacity_cycles_tests PRIVATE bzs_core Threads::Threads)
  bzs_add_native_test(bzs_cache_freshness_tests tests/cache_freshness_runner.cpp NO_TEST)
  target_link_libraries(bzs_cache_freshness_tests PRIVATE bzs_core Threads::Threads)
  # All original cases remain required; each partition keeps the 60s watchdog.
  foreach(shard RANGE 1 3)
    add_test(NAME cache_freshness_${shard} COMMAND bzs_cache_freshness_tests --shard ${shard} 3)
    set_tests_properties(cache_freshness_${shard} PROPERTIES TIMEOUT 60 LABELS "slow;cache_freshness")
  endforeach()
  bzs_add_native_test(bzs_contract_tests tests/contracts.cpp TEST contracts TIMEOUT 45
    ${CMAKE_CURRENT_SOURCE_DIR}/docs/planning/validation/contract_cases.json)
  target_link_libraries(bzs_contract_tests PRIVATE bzs_core)
  find_package(Python3 3.10 REQUIRED COMPONENTS Interpreter)
  add_test(NAME launcher_lifecycle COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/launcher_lifecycle.py)
  set_tests_properties(launcher_lifecycle PROPERTIES TIMEOUT 60)
  add_test(NAME cache_shard_contract COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/check_cache_shards.py --executable $<TARGET_FILE:bzs_cache_freshness_tests> --source ${CMAKE_CURRENT_SOURCE_DIR}/tests/cache_freshness.cpp)
  set_tests_properties(cache_shard_contract PROPERTIES TIMEOUT 30)
  add_test(NAME ci_reporting COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tools/test_ci_reporting.py)
  set_tests_properties(ci_reporting PROPERTIES TIMEOUT 15)
  add_test(NAME http_cli COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/integration.py
    --server $<TARGET_FILE:searchd> --cli $<TARGET_FILE:searchctl>
    --fixtures ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures)
  add_test(NAME http_revocation COMMAND ${Python3_EXECUTABLE} ${CMAKE_CURRENT_SOURCE_DIR}/tests/http_revocation.py
    --server $<TARGET_FILE:searchd> --cli $<TARGET_FILE:searchctl>
    --fixtures ${CMAKE_CURRENT_SOURCE_DIR}/tests/fixtures)
  set_tests_properties(http_revocation PROPERTIES TIMEOUT 90)
  # Python with the worker dependencies installed (`pip install -e python[test]`); e.g. a venv.
  set(BZS_PYTHON "${Python3_EXECUTABLE}" CACHE FILEPATH "Python interpreter used for worker/MCP tests")
  add_test(NAME e2e_web COMMAND ${BZS_PYTHON} ${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_web.py
    --server $<TARGET_FILE:searchd> --python ${BZS_PYTHON} --repo ${CMAKE_CURRENT_SOURCE_DIR})
  add_test(NAME e2e_map COMMAND ${BZS_PYTHON} ${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_map.py
    --server $<TARGET_FILE:searchd> --python ${BZS_PYTHON} --repo ${CMAKE_CURRENT_SOURCE_DIR})
  add_test(NAME e2e_crawl COMMAND ${BZS_PYTHON} ${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_crawl.py
    --server $<TARGET_FILE:searchd> --python ${BZS_PYTHON} --repo ${CMAKE_CURRENT_SOURCE_DIR})
  add_test(NAME e2e_crawl_recovery COMMAND ${BZS_PYTHON} ${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_crawl_recovery.py
    --server $<TARGET_FILE:searchd> --python ${BZS_PYTHON} --repo ${CMAKE_CURRENT_SOURCE_DIR})
  add_test(NAME e2e_session_recovery COMMAND ${BZS_PYTHON} ${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_session_recovery.py
    --server $<TARGET_FILE:searchd> --python ${BZS_PYTHON} --repo ${CMAKE_CURRENT_SOURCE_DIR})
  add_test(NAME e2e_revalidation COMMAND ${BZS_PYTHON} ${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_revalidation.py
    --server $<TARGET_FILE:searchd> --python ${BZS_PYTHON} --repo ${CMAKE_CURRENT_SOURCE_DIR})
  add_test(NAME e2e_cache_freshness COMMAND ${BZS_PYTHON} ${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_cache_freshness.py
    --server $<TARGET_FILE:searchd> --python ${BZS_PYTHON} --repo ${CMAKE_CURRENT_SOURCE_DIR})
  set_tests_properties(e2e_cache_freshness PROPERTIES TIMEOUT 180 SKIP_RETURN_CODE 77)
  add_test(NAME e2e_cache_policy_review COMMAND ${BZS_PYTHON} ${CMAKE_CURRENT_SOURCE_DIR}/tests/probe_http_policy.py
    --server $<TARGET_FILE:searchd> --python ${BZS_PYTHON} --repo ${CMAKE_CURRENT_SOURCE_DIR})
  set_tests_properties(e2e_cache_policy_review PROPERTIES TIMEOUT 180 SKIP_RETURN_CODE 77)
  add_test(NAME e2e_revocation COMMAND ${BZS_PYTHON} ${CMAKE_CURRENT_SOURCE_DIR}/tests/e2e_revocation.py
    --server $<TARGET_FILE:searchd> --python ${BZS_PYTHON} --repo ${CMAKE_CURRENT_SOURCE_DIR})
  set_tests_properties(e2e_revocation PROPERTIES TIMEOUT 180 SKIP_RETURN_CODE 77)
  execute_process(COMMAND ${BZS_PYTHON} -c "import pytest, aiohttp, lxml, trafilatura, mcp"
    RESULT_VARIABLE BZS_PY_DEPS OUTPUT_QUIET ERROR_QUIET)
  if(BZS_PY_DEPS EQUAL 0)
  add_test(NAME http_admission COMMAND ${BZS_PYTHON} ${CMAKE_CURRENT_SOURCE_DIR}/tests/http_admission.py
    --server $<TARGET_FILE:searchd> --python ${BZS_PYTHON} --repo ${CMAKE_CURRENT_SOURCE_DIR})
  set_tests_properties(http_admission PROPERTIES TIMEOUT 90)
    add_test(NAME python_worker COMMAND ${BZS_PYTHON} -m pytest -q -p no:cacheprovider tests
      WORKING_DIRECTORY ${CMAKE_CURRENT_SOURCE_DIR}/python)
  else()
    if(BZS_REQUIRE_PYTHON_TESTS)
      message(FATAL_ERROR "Required worker test dependencies are missing from ${BZS_PYTHON}")
    endif()
    message(STATUS "bzs861: worker dependencies not importable by ${BZS_PYTHON}; python_worker/e2e_web will be skipped "
                   "(pip install -e python[test], then -DBZS_PYTHON=<that python>)")
    add_test(NAME python_worker COMMAND ${BZS_PYTHON} -c "import sys; print('SKIP: worker deps missing'); sys.exit(77)")
  endif()
  set_tests_properties(http_cli PROPERTIES TIMEOUT 90)
  if(WIN32)
    # These are whole-process watchdogs for offline durable-store fixtures,
    # not product request deadlines. Windows Debug runs have reached the old
    # limits; retain every assertion and a bounded guard against a hung suite.
    set_tests_properties(crawl site_publication PROPERTIES TIMEOUT 120)
    # CL-10 (2026-11-13): the heavy persistent suites (core, sessions,
    # direct_shared, capacity_cycles, long_document_recall, cache_freshness_*)
    # now gate on RelWithDebInfo in Windows CI (see .github/workflows/ci.yml),
    # so their Windows watchdogs return to the Linux values - at most 1x the
    # Linux timeout, well inside the review's "<=3x Linux" bound. Reference
    # Windows 11 VM medians, 3 runs each, synchronous FULL kept (FULL vs OFF
    # measured within noise, so the durability pragma stays; full data in the
    # uncommitted measurement report
    # C:/tools/phase25-3-20261113/cl10-report.md):
    #   sessions             245.7 s Debug -> 33.1 s RelWithDebInfo, TIMEOUT 60
    #   direct_shared        247.1 s Debug -> 29.9 s RelWithDebInfo, TIMEOUT 90
    #   core                  74.6 s Debug -> 21.5 s RelWithDebInfo, TIMEOUT 45
    #   long_document_recall 215.5 s Debug -> 37.1 s RelWithDebInfo, TIMEOUT 300
    # Headroom 1.8x-8.1x at the Linux timeouts. capacity_cycles (180 s) and
    # cache_freshness_[123] (60 s) never had Windows overrides and keep the
    # Linux values. No assertion or test case is removed by this change: only
    # the whole-process watchdogs shrank; every suite still runs to completion
    # in the CI config it is gated on.
  endif()
  # 77 = dependencies missing -> reported as skipped, not passed.
  set_tests_properties(e2e_web PROPERTIES TIMEOUT 180 SKIP_RETURN_CODE 77)
  set_tests_properties(e2e_map PROPERTIES TIMEOUT 180 SKIP_RETURN_CODE 77)
  set_tests_properties(e2e_crawl PROPERTIES TIMEOUT 180 SKIP_RETURN_CODE 77)
  set_tests_properties(e2e_crawl_recovery PROPERTIES TIMEOUT 180 SKIP_RETURN_CODE 77)
  set_tests_properties(e2e_session_recovery PROPERTIES TIMEOUT 180 SKIP_RETURN_CODE 77)
  set_tests_properties(e2e_revalidation PROPERTIES TIMEOUT 180 SKIP_RETURN_CODE 77)
  set_tests_properties(python_worker PROPERTIES TIMEOUT 180 SKIP_RETURN_CODE 77)
endif()
install(TARGETS searchd searchctl RUNTIME DESTINATION bin)
