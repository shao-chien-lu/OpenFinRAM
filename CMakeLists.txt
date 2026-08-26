cmake_minimum_required(VERSION 3.22)
project(OpenFinRAM)

set(CMAKE_CXX_STANDARD 17)

# Add gdstk as subdirectory
add_subdirectory(third_party/gdstk)
add_subdirectory(third_party/argparse)

# Include directories
include_directories(include)
include_directories(third_party/plog/include)

# Collect source files
set(SOURCES
    src/main.cpp
    src/layermap.cpp
    src/cell_utils.cpp
    src/spice_generator.cpp
    src/synthesis_manager.cpp
    src/yosys_manager.cpp
    src/yosys_tcl_generator.cpp
    src/spice_converter.cpp
    src/spice_integrator.cpp
    src/spice_include_resolver.cpp
    src/spice_templates.cpp
    src/pex_runner.cpp
    src/capacitance_parser.cpp
    src/spice_simulator.cpp
    src/innovus_tcl_generator.cpp
    src/innovus_manager.cpp
    src/openroad_manager.cpp
    src/openroad_tcl_generator.cpp
    src/filler_generator.cpp
    src/siliconsmart_generator.cpp
    src/siliconsmart_manager.cpp
    src/lvs_runner.cpp
    src/lef_extractor.cpp
    src/liberty_estimator.cpp
    src/characterization_data.cpp
    src/main_config_helpers.cpp
    src/main_flow_helpers.cpp
    src/main_layout_helpers.cpp
    src/utils.cpp
    src/layout_generator.cpp
    src/lvs_manager.cpp
    src/lef_manager.cpp
)

# Create main executable
add_executable(OpenFinRAM ${SOURCES})

# Link libraries
find_package(Threads REQUIRED)
target_link_libraries(OpenFinRAM gdstk argparse Threads::Threads)

include(GNUInstallDirs)
file(MAKE_DIRECTORY "${CMAKE_BINARY_DIR}/scripts")
configure_file(
    "${CMAKE_SOURCE_DIR}/scripts/def_to_gds.py"
    "${CMAKE_BINARY_DIR}/scripts/def_to_gds.py"
    COPYONLY
)

install(TARGETS OpenFinRAM RUNTIME DESTINATION ${CMAKE_INSTALL_BINDIR})
install(
    PROGRAMS "${CMAKE_SOURCE_DIR}/scripts/def_to_gds.py"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/OpenFinRAM/scripts"
)

# ---------------------------------------------------------------------------
# Tests (ctest). The harness scripts cd to the repo root themselves, so they
# work from any build directory. External tools are checked inside the
# scripts; missing tools surface as a clear test failure message.
#   tests/run_decode_check.sh : exhaustive RTL decode-correctness (Icarus)
#   tests/run_equiv_check.sh  : post-synthesis RTL<->netlist equivalence (Yosys)
# ---------------------------------------------------------------------------
include(CTest)
if(BUILD_TESTING)
    add_subdirectory(third_party/googletest EXCLUDE_FROM_ALL)
    add_subdirectory(tests)

    add_test(
        NAME decode_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_decode_check.sh
    )
    set_tests_properties(decode_check PROPERTIES TIMEOUT 600)

    add_test(
        NAME equiv_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_equiv_check.sh
    )
    set_tests_properties(equiv_check PROPERTIES TIMEOUT 600)

    # Skips (exit 77) when the KLayout Python bindings are unavailable.
    add_test(
        NAME def2gds_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_def2gds_check.sh
    )
    set_tests_properties(def2gds_check PROPERTIES TIMEOUT 300 SKIP_RETURN_CODE 77)

    # P0 characterization-JSON gate: a JSON carrying the estimated constants
    # regenerates the estimated .lib byte-for-byte; measured values and
    # per-sdel variants override as documented.
    add_test(
        NAME char_json_equiv_check
        COMMAND ${CMAKE_COMMAND} -E env
            OPENFINRAM_BIN=${CMAKE_BINARY_DIR}/OpenFinRAM
            bash ${CMAKE_SOURCE_DIR}/tests/run_char_json_check.sh
    )
    set_tests_properties(char_json_equiv_check PROPERTIES TIMEOUT 600)

    # P6 Table III gate (Xyce transient -> JSON -> .lib -> validator).
    # Skips (exit 77) when Xyce is unavailable.
    add_test(
        NAME table3_gate
        COMMAND ${CMAKE_COMMAND} -E env
            OPENFINRAM_BIN=${CMAKE_BINARY_DIR}/OpenFinRAM
            bash ${CMAKE_SOURCE_DIR}/tests/run_table3_gate.sh
    )
    set_tests_properties(table3_gate PROPERTIES TIMEOUT 1200 SKIP_RETURN_CODE 77)

    # P2 constraints gate: sweep load-monotonicity, Liberty-pushout
    # setup/hold, min_period and pin caps. Skips (exit 77) without Xyce.
    add_test(
        NAME constraints_gate
        COMMAND ${CMAKE_COMMAND} -E env
            bash ${CMAKE_SOURCE_DIR}/tests/run_constraints_gate.sh
    )
    set_tests_properties(constraints_gate PROPERTIES TIMEOUT 1200 SKIP_RETURN_CODE 77)

    # P4 corner gate: SS/TT/FF sweeps -> three libs, ordered and
    # corner-tagged. Skips (exit 77) without Xyce.
    add_test(
        NAME corner_gate
        COMMAND ${CMAKE_COMMAND} -E env
            OPENFINRAM_BIN=${CMAKE_BINARY_DIR}/OpenFinRAM
            bash ${CMAKE_SOURCE_DIR}/tests/run_corner_gate.sh
    )
    set_tests_properties(corner_gate PROPERTIES TIMEOUT 1800 SKIP_RETURN_CODE 77)

    # P5 interpolation gate: ladder {64,256} -> interpolated 128 validated
    # against a held-out measured d128 sweep. Skips (exit 77) without Xyce.
    add_test(
        NAME interp_gate
        COMMAND ${CMAKE_COMMAND} -E env
            bash ${CMAKE_SOURCE_DIR}/tests/run_interp_gate.sh
    )
    set_tests_properties(interp_gate PROPERTIES TIMEOUT 1800 SKIP_RETURN_CODE 77)
endif()
