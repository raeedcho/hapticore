include(GoogleTest)

add_executable(test_triple_buffer test_triple_buffer.cpp)
target_link_libraries(test_triple_buffer PRIVATE haptic_server_lib GTest::gtest_main)
gtest_discover_tests(test_triple_buffer)

add_executable(test_force_fields test_force_fields.cpp)
target_link_libraries(test_force_fields PRIVATE haptic_server_lib GTest::gtest_main)
gtest_discover_tests(test_force_fields)

if(MOCK_HARDWARE)
    add_executable(test_dhd_mock test_dhd_mock.cpp)
    target_link_libraries(test_dhd_mock PRIVATE haptic_server_lib GTest::gtest_main)
    gtest_discover_tests(test_dhd_mock)
endif()
