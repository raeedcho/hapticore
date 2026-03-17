#pragma once

#include <string>
#include <stop_token>

#include <zmq.hpp>

#include "state_data.hpp"
#include "triple_buffer.hpp"

class PublisherThread {
public:
    PublisherThread(TripleBuffer<HapticStateData>& state_buffer,
                    const std::string& pub_address,
                    double publish_rate_hz,
                    zmq::context_t& ctx);

    void run(std::stop_token stop);

private:
    TripleBuffer<HapticStateData>& state_buffer_;
    std::string pub_address_;
    double publish_rate_hz_;
    zmq::context_t& ctx_;
};
