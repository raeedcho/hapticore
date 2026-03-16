# Tests that depend on the DHD mock implementation should only be built
# when MOCK_HARDWARE is enabled. This avoids link errors in configurations
# that use the real DHD implementation (dhd_real.cpp) and do not provide
# DhdMock symbols.

if(MOCK_HARDWARE)
    add_executable(test_dhd_mock test_dhd_mock.cpp)
    target_link_libraries(test_dhd_mock PRIVATE haptic_server gtest_main)
    add_test(NAME test_dhd_mock COMMAND test_dhd_mock)
endif()
include(GoogleTest)

add_executable(test_triple_buffer test_triple_buffer.cpp)
target_link_libraries(test_triple_buffer PRIVATE haptic_server_lib GTest::gtest_main)
gtest_discover_tests(test_triple_buffer)

add_executable(test_force_fields test_force_fields.cpp)
target_link_libraries(test_force_fields PRIVATE haptic_server_lib GTest::gtest_main)
gtest_discover_tests(test_force_fields)

add_executable(test_dhd_mock test_dhd_mock.cpp)
target_link_libraries(test_dhd_mock PRIVATE haptic_server_lib GTest::gtest_main)
gtest_discover_tests(test_dhd_mock)
