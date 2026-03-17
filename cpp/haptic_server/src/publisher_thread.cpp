#include "publisher_thread.hpp"

#include <chrono>
#include <thread>

#include <zmq.hpp>

PublisherThread::PublisherThread(TripleBuffer<HapticStateData>& state_buffer,
                                 const std::string& pub_address,
                                 double publish_rate_hz)
    : state_buffer_(state_buffer)
    , pub_address_(pub_address)
    , publish_rate_hz_(publish_rate_hz)
{}

void PublisherThread::run(std::stop_token stop) {
    zmq::context_t ctx(1);
    zmq::socket_t pub(ctx, zmq::socket_type::pub);
    pub.set(zmq::sockopt::linger, 0);
    pub.bind(pub_address_);

    auto interval = std::chrono::microseconds(
        static_cast<long long>(1'000'000.0 / publish_rate_hz_));

    while (!stop.stop_requested()) {
        if (state_buffer_.swap_read_buffer()) {
            const auto& state = state_buffer_.read_buffer();
            msgpack::sbuffer buf;
            state.pack(buf);

            // Send multipart: [b"state", packed_bytes]
            zmq::message_t topic_msg("state", 5);
            zmq::message_t data_msg(buf.data(), buf.size());

            pub.send(topic_msg, zmq::send_flags::sndmore);
            pub.send(data_msg, zmq::send_flags::none);
        }

        std::this_thread::sleep_for(interval);
    }

    pub.close();
    ctx.close();
}
