cmake_minimum_required(VERSION 3.16)
project(haptic_server LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ---------- Compiler warnings ----------
add_compile_options(-Wall -Wextra -Wpedantic)
if(CMAKE_BUILD_TYPE STREQUAL "Debug")
  add_compile_options(-Werror)
endif()

# ---------- CPM dependency manager ----------
include(cmake/CPM.cmake)

CPMAddPackage(
  NAME googletest
  GITHUB_REPOSITORY google/googletest
  VERSION 1.15.2
  OPTIONS "INSTALL_GTEST OFF"
)

CPMAddPackage(
  NAME msgpack-cxx
  GITHUB_REPOSITORY msgpack/msgpack-c
  GIT_TAG cpp-6.1.1
  OPTIONS "MSGPACK_USE_BOOST OFF"
          "MSGPACK_BUILD_TESTS OFF"
          "MSGPACK_BUILD_EXAMPLES OFF"
)

# ---------- Hardware mock option ----------
option(MOCK_HARDWARE "Use mock DHD instead of real Force Dimension SDK" ON)

# ---------- Static library (all sources except main.cpp) ----------
# No library sources yet — use INTERFACE so dependents can inherit include paths
# and linked libraries. Will become a STATIC library once sources are added.
add_library(haptic_server_lib INTERFACE)

target_include_directories(haptic_server_lib INTERFACE
  ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_link_libraries(haptic_server_lib INTERFACE
  msgpack-cxx
)

if(MOCK_HARDWARE)
  target_compile_definitions(haptic_server_lib INTERFACE HAPTIC_MOCK_HARDWARE)
else()
  # Locate Force Dimension SDK via FD_SDK_DIR environment variable
  if(NOT DEFINED ENV{FD_SDK_DIR})
    message(FATAL_ERROR
      "FD_SDK_DIR environment variable not set. "
      "Set it to the Force Dimension SDK root, or build with -DMOCK_HARDWARE=ON."
    )
  endif()
  set(FD_SDK_DIR $ENV{FD_SDK_DIR})
  target_include_directories(haptic_server_lib INTERFACE ${FD_SDK_DIR}/include)
  target_link_directories(haptic_server_lib INTERFACE ${FD_SDK_DIR}/lib)
  target_link_libraries(haptic_server_lib INTERFACE dhd drd)
endif()

# Platform libraries
target_link_libraries(haptic_server_lib INTERFACE pthread)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  target_link_libraries(haptic_server_lib INTERFACE rt)
endif()

# ---------- Main executable ----------
add_executable(haptic_server src/main.cpp)
target_link_libraries(haptic_server PRIVATE haptic_server_lib)

# ---------- Tests ----------
enable_testing()
add_subdirectory(tests)
