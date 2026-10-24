set(OSSIE_VANE_DISTRIBUTED
    ON
    CACHE BOOL "Build Ossie with Vane's coordinator context policy" FORCE)

duckdb_extension_load(
  ossie
  SOURCE_DIR
  ${CMAKE_CURRENT_LIST_DIR}/vane
  INCLUDE_DIR
  ${CMAKE_CURRENT_LIST_DIR}/src/include
  TEST_DIR
  ${CMAKE_CURRENT_LIST_DIR}/test)

if(BUILD_UNITTESTS)
  duckdb_extension_load(tpcds)
endif()
