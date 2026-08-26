# Unit tests (googletest). Golden files live in tests/golden/.
add_executable(unit_tests
    unit/test_tcl_generators.cpp
    unit/test_lef_extractor.cpp
    unit/test_characterization.cpp
    ${CMAKE_SOURCE_DIR}/src/characterization_data.cpp
    ${CMAKE_SOURCE_DIR}/src/liberty_estimator.cpp
    ${CMAKE_SOURCE_DIR}/src/yosys_tcl_generator.cpp
    ${CMAKE_SOURCE_DIR}/src/openroad_tcl_generator.cpp
    ${CMAKE_SOURCE_DIR}/src/lef_extractor.cpp
    ${CMAKE_SOURCE_DIR}/src/utils.cpp
)

target_include_directories(unit_tests PRIVATE
    ${CMAKE_SOURCE_DIR}/include
    ${CMAKE_SOURCE_DIR}/third_party/plog/include
)

target_compile_definitions(unit_tests PRIVATE REPO_ROOT="${CMAKE_SOURCE_DIR}")

target_link_libraries(unit_tests gtest_main gdstk)

include(GoogleTest)
gtest_discover_tests(unit_tests)
