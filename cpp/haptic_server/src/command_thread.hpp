#pragma once

#include <functional>
#include <string>
#include <stop_token>

#include <zmq.hpp>

#include "command_data.hpp"

class CommandThread {
public:
    using Handler = std::function<CommandResponseData(const CommandData&)>;

    CommandThread(const std::string& router_address,
                  Handler handler,
                  zmq::context_t& ctx);

    void run(std::stop_token stop);

private:
    std::string router_address_;
    Handler handler_;
    zmq::context_t& ctx_;
};
