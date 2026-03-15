#pragma once
#include "force_field.hpp"
#include <memory>
#include <string>

std::unique_ptr<ForceField> create_field(const std::string& type_name);
