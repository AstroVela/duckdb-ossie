cmake_minimum_required(VERSION 3.29)
project(ossie LANGUAGES CXX)

if(NOT OSSIE_VANE_DISTRIBUTED)
  message(FATAL_ERROR "The Vane entry point requires OSSIE_VANE_DISTRIBUTED=ON")
endif()

set(OSSIE_ROOT ${CMAKE_CURRENT_LIST_DIR}/..)
set(OSSIE_SOURCES
    ossie_vane_extension.cpp
    ${OSSIE_ROOT}/src/model/parser.cpp
    ${OSSIE_ROOT}/src/model/validate.cpp
    ${OSSIE_ROOT}/src/model/graph.cpp
    ${OSSIE_ROOT}/src/model/catalog.cpp
    ${OSSIE_ROOT}/src/model/yaml.cpp
    ${OSSIE_ROOT}/src/compile/compile.cpp
    ${OSSIE_ROOT}/src/functions/load.cpp
    ${OSSIE_ROOT}/src/functions/compile.cpp
    ${OSSIE_ROOT}/src/functions/describe.cpp
    ${OSSIE_ROOT}/src/functions/query.cpp)

include_directories(${OSSIE_ROOT}/src/include
                    ${OSSIE_ROOT}/third_party/fkYAML/include)
build_static_extension(ossie ${OSSIE_SOURCES})
build_loadable_extension(ossie "" ${OSSIE_SOURCES})

foreach(target ossie_extension ossie_loadable_extension)
  # Inherit the parent DuckDB build's C++ mode, including constexpr storage.
  target_compile_features(${target} PRIVATE cxx_std_11)
  target_compile_definitions(${target} PRIVATE OSSIE_VANE_DISTRIBUTED)
endforeach()

if(OSSIE_VANE_ARTIFACT_DIRECTORY)
  add_custom_target(
    vane_ossie_artifact
    COMMAND ${CMAKE_COMMAND} -E make_directory
            "${OSSIE_VANE_ARTIFACT_DIRECTORY}"
    COMMAND
      ${CMAKE_COMMAND} -E copy_if_different
      $<TARGET_FILE:ossie_loadable_extension>
      "${OSSIE_VANE_ARTIFACT_DIRECTORY}/ossie.duckdb_extension"
    DEPENDS ossie_loadable_extension
    VERBATIM)
endif()

install(
  TARGETS ossie_extension
  EXPORT "${DUCKDB_EXPORT_SET}"
  LIBRARY DESTINATION "${INSTALL_LIB_DIR}"
  ARCHIVE DESTINATION "${INSTALL_LIB_DIR}")
