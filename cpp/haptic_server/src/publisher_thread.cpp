#include "publisher_thread.hpp"

#include <chrono>
#include <thread>

PublisherThread::PublisherThread(TripleBuffer<HapticStateData>& state_buffer,
                                 const std::string& pub_address,
                                 double publish_rate_hz,
                                 zmq::context_t& ctx)
    : state_buffer_(state_buffer)
    , pub_address_(pub_address)
    , publish_rate_hz_(publish_rate_hz)
    , ctx_(ctx)
{}

void PublisherThread::run(std::atomic<bool>& stop_requested) {
    zmq::socket_t pub(ctx_, zmq::socket_type::pub);
    pub.set(zmq::sockopt::linger, 0);
    pub.bind(pub_address_);

    auto interval = std::chrono::microseconds(
        static_cast<long long>(1'000'000.0 / publish_rate_hz_));

    while (!stop_requested.load(std::memory_order_relaxed)) {
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
}
