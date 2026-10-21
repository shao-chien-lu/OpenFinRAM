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
# Generated cell libraries. The 8T bitcell family, the wordline arrays, the 8T
# and 6T IO blocks and the wordline driver slices are generator output, written
# into tech/gds where the flow reads them; git tracks only their digests
# (tech/gds/digests.json, scripts/tech_gds.py). The generators need gdstk and
# chipforge_asap7, which the 2RW and 6T flows need anyway.
# ---------------------------------------------------------------------------
if(EXISTS "${CMAKE_SOURCE_DIR}/.venv/bin/python")
    set(OPENFINRAM_PYTHON "${CMAKE_SOURCE_DIR}/.venv/bin/python")
else()
    find_package(Python3 COMPONENTS Interpreter)
    set(OPENFINRAM_PYTHON "${Python3_EXECUTABLE}")
endif()
set(TECH_GDS_FILES)
foreach(library sram_cell_8t sram_cell_8t_edges sram_cell_8t_tap sram_wordline_arrays
                sram_8t_iocolumn sram_8t_wl_slices sram_6t_iocolumn)
    list(APPEND TECH_GDS_FILES "${CMAKE_SOURCE_DIR}/tech/gds/${library}.gds")
endforeach()
file(GLOB TECH_GDS_GENERATORS CONFIGURE_DEPENDS "${CMAKE_SOURCE_DIR}/scripts/generate_asap7_*.py")
execute_process(
    COMMAND "${OPENFINRAM_PYTHON}" -c "import gdstk, chipforge_asap7"
    RESULT_VARIABLE TECH_GDS_PYTHON_RESULT OUTPUT_QUIET ERROR_QUIET
)
if(TECH_GDS_PYTHON_RESULT EQUAL 0)
    add_custom_command(
        OUTPUT ${TECH_GDS_FILES}
        COMMAND "${OPENFINRAM_PYTHON}" "${CMAKE_SOURCE_DIR}/scripts/tech_gds.py"
        DEPENDS "${CMAKE_SOURCE_DIR}/scripts/tech_gds.py" ${TECH_GDS_GENERATORS}
                "${CMAKE_SOURCE_DIR}/tech/gds/srambank_32b_boundary_2.gds"
        WORKING_DIRECTORY "${CMAKE_SOURCE_DIR}"
        COMMENT "Generating the ASAP7 cell libraries in tech/gds"
        VERBATIM
    )
    add_custom_target(tech_gds ALL DEPENDS ${TECH_GDS_FILES})
else()
    message(WARNING "${OPENFINRAM_PYTHON} cannot import gdstk and chipforge_asap7, so the "
        "generated cell libraries in tech/gds are not built; the 2RW and 6T flows need "
        "them (scripts/tech_gds.py writes them).")
endif()

# ---------------------------------------------------------------------------
# Tests (ctest). The harness scripts cd to the repo root themselves, so they
# work from any build directory. External tools are checked inside the
# scripts; missing tools surface as a clear test failure message.
#   tests/run_decode_check.sh : exhaustive RTL decode-correctness (Icarus)
#   tests/run_equiv_check.sh  : post-synthesis RTL<->netlist equivalence (Yosys)
# ---------------------------------------------------------------------------
include(CTest)
add_custom_target(install_gdscheck
    COMMAND bash ${CMAKE_SOURCE_DIR}/scripts/install_gdscheck.sh
    COMMENT "Install the pinned gdscheck DRC executable under build/tools/gdscheck"
    USES_TERMINAL
)
if(BUILD_TESTING)
    find_package(Python3 COMPONENTS Interpreter REQUIRED)
    add_test(NAME gdscheck_adapter_check
        COMMAND ${Python3_EXECUTABLE} -m unittest discover
            -s ${CMAKE_SOURCE_DIR}/tests -p test_drc.py
    )
    set_tests_properties(gdscheck_adapter_check PROPERTIES TIMEOUT 120 LABELS unit)
    add_subdirectory(third_party/googletest EXCLUDE_FROM_ALL)
    add_subdirectory(tests)

    add_test(
        NAME decode_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_decode_check.sh
    )
    set_tests_properties(decode_check PROPERTIES TIMEOUT 600)

    add_test(
        NAME decode_dp_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_decode_dp_check.sh
    )
    set_tests_properties(decode_dp_check PROPERTIES TIMEOUT 600)

    add_test(
        NAME decode_1p_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_decode_1p_check.sh
    )
    set_tests_properties(decode_1p_check PROPERTIES TIMEOUT 600)

    add_test(
        NAME asap7_8t_decoder_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_8t_decoder_check.sh
    )
    set_tests_properties(asap7_8t_decoder_check PROPERTIES TIMEOUT 300 SKIP_RETURN_CODE 77)

    add_test(
        NAME asap7_8t_decoder_physical_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_8t_decoder_physical_check.sh
    )
    set_tests_properties(asap7_8t_decoder_physical_check PROPERTIES TIMEOUT 600 SKIP_RETURN_CODE 77)

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

    # ASAP7 8T bitcell/edge family: regenerate the GDS, extract
    # devices/connectivity, check cap abutment, and compare recorded digests.
    add_test(
        NAME asap7_8t_bitcell_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_8t_bitcell_check.sh
    )
    set_tests_properties(asap7_8t_bitcell_check PROPERTIES
        TIMEOUT 300
        SKIP_RETURN_CODE 77
    )

    # The built cell libraries in tech/gds against their recorded digests.
    add_test(
        NAME asap7_tech_gds_check
        COMMAND ${OPENFINRAM_PYTHON} ${CMAKE_SOURCE_DIR}/scripts/tech_gds.py --check ${TECH_GDS_FILES}
    )
    set_tests_properties(asap7_tech_gds_check PROPERTIES TIMEOUT 120 SKIP_RETURN_CODE 77)

    # Device-level regression at the ASAP7 TT corner.  Exercises both port
    # interfaces through precharge, opposite-polarity reads/writes, concurrent
    # different-address access, and same-address read/read through shared 8T
    # cells.  Skips when Xyce is unavailable.
    add_test(
        NAME asap7_8t_ioprech_spice_check
        COMMAND ${CMAKE_COMMAND} -E env
            IOPRECH_SPICE_DUMPER=$<TARGET_FILE:dump_8t_ioprech_spice>
            bash ${CMAKE_SOURCE_DIR}/tests/run_8t_ioprech_spice_check.sh
    )
    set_tests_properties(asap7_8t_ioprech_spice_check PROPERTIES
        TIMEOUT 600
        SKIP_RETURN_CODE 77
    )

    # Geometry/parser checks run even on machines without a SPICE simulator.
    add_test(
        NAME asap7_8t_snm_analysis_check
        COMMAND python3 ${CMAKE_SOURCE_DIR}/tests/tools/test_snm_from_sweep.py
    )
    set_tests_properties(asap7_8t_snm_analysis_check PROPERTIES TIMEOUT 30)

    # Cell-level stability for the true-dual-port 8T: write trip per port,
    # hold/read-A/read-B/read-both SNM and DC sweep convergence at SS/TT/FF.
    # The macro contract declares concurrent same-address read/read legal and
    # the cell's strength ratios are inherited from the single-port 6T, so
    # this guards nominal cell margins, not mismatch yield. Bitcell only, no
    # periphery, so a failure points at the cell. Skips without Xyce.
    add_test(
        NAME asap7_8t_stability_check
        COMMAND ${CMAKE_COMMAND} -E env
            IOPRECH_SPICE_DUMPER=$<TARGET_FILE:dump_8t_ioprech_spice>
            bash ${CMAKE_SOURCE_DIR}/tests/run_8t_stability_check.sh
    )
    set_tests_properties(asap7_8t_stability_check PROPERTIES
        TIMEOUT 1200
        SKIP_RETURN_CODE 77
    )

    # Academic-style sramcol_xN / array_xNxM hierarchy for parameterized 8T
    # wordlines, with the same builder covering non-standard 6T sizes.
    add_test(
        NAME asap7_wordline_array_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_wordline_array_check.sh
    )
    set_tests_properties(asap7_wordline_array_check PROPERTIES
        TIMEOUT 300
        SKIP_RETURN_CODE 77
    )

    # Routed dual-port IO column: two independent IO cores connected to the
    # same capped 8T half-arrays using layer-separated M2/M4/M5 paths.
    add_test(
        NAME asap7_8t_iocolumn_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_8t_iocolumn_check.sh
    )
    set_tests_properties(asap7_8t_iocolumn_check PROPERTIES
        TIMEOUT 300
        SKIP_RETURN_CODE 77
    )

    # The wordline driver slice ladder the 2RW macro's strips are built from.
    add_test(
        NAME asap7_8t_wl_slices_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_8t_wl_slices_check.sh
    )
    set_tests_properties(asap7_8t_wl_slices_check PROPERTIES
        TIMEOUT 600
        SKIP_RETURN_CODE 77
    )

    # Device-level DRC over the handcrafted cell libraries.  OpenROAD's
    # detailed_route DRC only covers what the router drew; nothing checked the
    # cell interiors or their abutment until this gate.  It is a ratchet
    # against tests/golden/asap7_8t_drc_gdscheck.json, not a clean-DRC claim.
    add_test(
        NAME asap7_8t_device_drc_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_8t_device_drc_check.sh
    )
    set_tests_properties(asap7_8t_device_drc_check PROPERTIES
        TIMEOUT 900
        SKIP_RETURN_CODE 77
        LABELS "drc;integration"
    )

    add_test(
        NAME asap7_2rw_physical_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_2rw_physical_check.sh
    )
    set_tests_properties(asap7_2rw_physical_check PROPERTIES
        TIMEOUT 300 SKIP_RETURN_CODE 77
    )
    add_test(
        NAME asap7_2rw_macro_check
        COMMAND ${CMAKE_COMMAND} -E env OPENFINRAM_BIN=$<TARGET_FILE:OpenFinRAM>
            bash ${CMAKE_SOURCE_DIR}/tests/run_2rw_macro_check.sh
    )
    set_tests_properties(asap7_2rw_macro_check PROPERTIES
        TIMEOUT 1200 SKIP_RETURN_CODE 77 LABELS integration RUN_SERIAL TRUE
    )
    add_test(
        NAME asap7_6t_physical_check
        COMMAND ${CMAKE_COMMAND} -E env bash ${CMAKE_SOURCE_DIR}/tests/run_6t_physical_check.sh
    )
    set_tests_properties(asap7_6t_physical_check PROPERTIES
        TIMEOUT 600 SKIP_RETURN_CODE 77
    )
    add_test(
        NAME asap7_6t_macro_check
        COMMAND ${CMAKE_COMMAND} -E env OPENFINRAM_BIN=$<TARGET_FILE:OpenFinRAM>
            bash ${CMAKE_SOURCE_DIR}/tests/run_6t_macro_check.sh
    )
    set_tests_properties(asap7_6t_macro_check PROPERTIES
        TIMEOUT 1800 SKIP_RETURN_CODE 77 LABELS integration RUN_SERIAL TRUE
    )

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
