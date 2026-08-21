cmake_minimum_required(VERSION 3.21)
project(haptic_server LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

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
  GIT_TAG cpp-7.0.0
  OPTIONS "MSGPACK_USE_BOOST OFF"
          "MSGPACK_BUILD_TESTS OFF"
          "MSGPACK_BUILD_EXAMPLES OFF"
)

CPMAddPackage(
  NAME cppzmq
  VERSION 4.10.0
  GITHUB_REPOSITORY zeromq/cppzmq
  OPTIONS "CPPZMQ_BUILD_TESTS OFF"
)

# Suppress warnings from cppzmq headers (third-party code we don't control).
# Without this, Clang's -Wdeprecated-literal-operator fires on zmq.hpp's
# operator"" _zbuf declarations and -Werror promotes it to a build failure.
if(TARGET cppzmq)
  get_target_property(_cppzmq_inc cppzmq INTERFACE_INCLUDE_DIRECTORIES)
  set_target_properties(cppzmq PROPERTIES
    INTERFACE_SYSTEM_INCLUDE_DIRECTORIES "${_cppzmq_inc}"
  )
endif()

CPMAddPackage(
  NAME box2d
  GITHUB_REPOSITORY erincatto/box2d
  VERSION 3.0.0
  OPTIONS "BOX2D_SAMPLES OFF" "BOX2D_UNIT_TESTS OFF" "BOX2D_DOCS OFF"
)

# ---------- Hardware mock option ----------
option(MOCK_HARDWARE "Use mock DHD instead of real Force Dimension SDK" ON)

# ---------- Static library (all sources except main.cpp) ----------
set(LIB_SOURCES
  src/force_fields/null_field.cpp
  src/force_fields/constant_field.cpp
  src/force_fields/spring_damper_field.cpp
  src/force_fields/workspace_limit_field.cpp
  src/force_fields/cart_pendulum_field.cpp
  src/force_fields/channel_field.cpp
  src/force_fields/composite_field.cpp
  src/force_fields/physics_field.cpp
  src/force_fields/field_factory.cpp
  src/publisher_thread.cpp
  src/command_thread.cpp
  src/haptic_thread.cpp
)

if(MOCK_HARDWARE)
  list(APPEND LIB_SOURCES src/dhd_mock.cpp)
else()
  list(APPEND LIB_SOURCES src/dhd_real.cpp)
endif()

add_library(haptic_server_lib STATIC ${LIB_SOURCES})

# ---------- Compiler warnings (target-specific, not applied to third-party deps) ----------
target_compile_options(haptic_server_lib PRIVATE -Wall -Wextra -Wpedantic -Werror)

target_include_directories(haptic_server_lib PUBLIC
  ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_link_libraries(haptic_server_lib PUBLIC
  msgpack-cxx
  cppzmq
  box2d
)

if(MOCK_HARDWARE)
  target_compile_definitions(haptic_server_lib PUBLIC HAPTIC_MOCK_HARDWARE)
else()
  # Locate Force Dimension SDK via FD_SDK_DIR environment variable
  if(NOT DEFINED ENV{FD_SDK_DIR})
    message(FATAL_ERROR
      "FD_SDK_DIR environment variable not set. "
      "Set it to the Force Dimension SDK root, or build with -DMOCK_HARDWARE=ON."
    )
  endif()
  set(FD_SDK_DIR $ENV{FD_SDK_DIR})
  target_include_directories(haptic_server_lib PUBLIC ${FD_SDK_DIR}/include)

  # SDK prebuilt libraries live in a platform/arch/compiler subdirectory of
  # lib/release, following the SDK's own Makefile.common convention:
  #   Linux: lib/release/lin-<arch>-gcc    macOS: lib/release/mac-<arch>-clang
  # Verified against SDK 3.17.7: lin-x86_64-gcc on the rig, mac-arm64-clang on
  # Apple Silicon (CMAKE_SYSTEM_PROCESSOR is "arm64" there, matching the SDK).
  if(APPLE)
    set(FD_LIB_SUBDIR lib/release/mac-${CMAKE_SYSTEM_PROCESSOR}-clang)
  else()
    set(FD_LIB_SUBDIR lib/release/lin-${CMAKE_SYSTEM_PROCESSOR}-gcc)
  endif()

  # Prefer the static archives on both platforms. find_library gives absolute
  # paths, so a missing SDK fails at configure time with a useful message
  # instead of producing a bogus -L search path and an -ldhd link error.
  # The suffix override matters on macOS, where the SDK also ships
  # version-suffixed .dylib files with no unversioned symlink.
  set(_fd_saved_suffixes ${CMAKE_FIND_LIBRARY_SUFFIXES})
  set(CMAKE_FIND_LIBRARY_SUFFIXES ".a")
  find_library(FD_DHD_LIBRARY NAMES dhd
    HINTS ${FD_SDK_DIR} PATH_SUFFIXES ${FD_LIB_SUBDIR} NO_DEFAULT_PATH)
  find_library(FD_DRD_LIBRARY NAMES drd
    HINTS ${FD_SDK_DIR} PATH_SUFFIXES ${FD_LIB_SUBDIR} NO_DEFAULT_PATH)
  set(CMAKE_FIND_LIBRARY_SUFFIXES ${_fd_saved_suffixes})

  if(NOT FD_DHD_LIBRARY OR NOT FD_DRD_LIBRARY)
    message(FATAL_ERROR
      "Force Dimension libraries not found in ${FD_SDK_DIR}/${FD_LIB_SUBDIR}. "
      "Run `ls ${FD_SDK_DIR}/lib/release` and confirm the subdirectory name "
      "matches this platform, or build with -DMOCK_HARDWARE=ON."
    )
  endif()

  target_link_libraries(haptic_server_lib PUBLIC ${FD_DHD_LIBRARY} ${FD_DRD_LIBRARY})

  # libdhd.a's USB transport is a transitive dependency a static archive cannot
  # carry, so it must be linked explicitly. Linux uses libusb-1.0; the macOS
  # SDK uses Apple's native IOKit USB stack instead and needs no libusb (delta.x
  # manual section 6.3: "uses Apple's native USB drivers ... No further
  # installation is required").
  if(APPLE)
    target_link_libraries(haptic_server_lib PUBLIC
      "-framework IOKit" "-framework CoreFoundation")
  else()
    target_link_libraries(haptic_server_lib PUBLIC usb-1.0)
  endif()
endif()

# Platform libraries
find_package(Threads REQUIRED)
target_link_libraries(haptic_server_lib PUBLIC Threads::Threads)
if(CMAKE_SYSTEM_NAME STREQUAL "Linux")
  target_link_libraries(haptic_server_lib PUBLIC rt)
endif()

# ---------- Main executable ----------
add_executable(haptic_server src/main.cpp)
target_compile_options(haptic_server PRIVATE -Wall -Wextra -Wpedantic -Werror)
target_link_libraries(haptic_server PRIVATE haptic_server_lib)

# On Linux real-hardware builds, set CAP_SYS_NICE for real-time scheduling.
# Mock builds skip this — they don't need RT priority and shouldn't trigger sudo.
# If neither plain setcap nor passwordless sudo works, print a loud warning so
# the user knows real-time scheduling will be unavailable, but don't fail the
# build (so devs without sudo configured aren't blocked entirely).
if(CMAKE_SYSTEM_NAME STREQUAL "Linux" AND NOT MOCK_HARDWARE)
  add_custom_command(TARGET haptic_server POST_BUILD
    COMMAND setcap cap_sys_nice=eip $<TARGET_FILE:haptic_server> 2>/dev/null
            || sudo -n setcap cap_sys_nice=eip $<TARGET_FILE:haptic_server> 2>/dev/null
            || ${CMAKE_COMMAND} -E echo "WARNING: setcap failed. Real-time scheduling will be unavailable. Run manually: sudo setcap cap_sys_nice=eip $<TARGET_FILE:haptic_server>"
    COMMAND ${CMAKE_COMMAND} -E true
    COMMENT "Setting CAP_SYS_NICE on haptic_server"
    VERBATIM
  )
endif()

# ---------- Tests ----------
enable_testing()
add_subdirectory(tests)
