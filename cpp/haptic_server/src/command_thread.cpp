#include "command_thread.hpp"

#include <iostream>
#include <vector>

CommandThread::CommandThread(const std::string& router_address,
                             Handler handler,
                             zmq::context_t& ctx)
    : router_address_(router_address)
    , handler_(std::move(handler))
    , ctx_(ctx)
{}

void CommandThread::run(std::atomic<bool>& stop_requested) {
    zmq::socket_t router(ctx_, zmq::socket_type::router);
    router.set(zmq::sockopt::linger, 0);
    router.bind(router_address_);

    zmq::pollitem_t items[] = {
        {static_cast<void*>(router), 0, ZMQ_POLLIN, 0}
    };

    while (!stop_requested.load(std::memory_order_relaxed)) {
        zmq::poll(items, 1, std::chrono::milliseconds(100));

        if (!(items[0].revents & ZMQ_POLLIN)) continue;

        // ROUTER receives: [identity, empty_frame, payload]
        std::vector<zmq::message_t> frames;
        zmq::recv_result_t more;
        do {
            zmq::message_t frame;
            more = router.recv(frame, zmq::recv_flags::none);
            if (!more.has_value()) break;
            frames.push_back(std::move(frame));
        } while (router.get(zmq::sockopt::rcvmore));

        // Require exactly 3 frames: [identity, empty_delimiter, payload]
        if (frames.size() != 3 || frames[1].size() != 0) {
            std::cerr << "Warning: malformed message (expected 3 frames with empty delimiter, got "
                      << frames.size() << " frames)\n";
            continue;
        }

        // frames[0] = identity, frames[1] = empty delimiter, frames[2] = payload
        auto& identity = frames[0];
        auto& payload = frames[2];

        try {
            auto cmd = CommandData::unpack(
                static_cast<const char*>(payload.data()),
                payload.size());

            auto response = handler_(cmd);

            msgpack::sbuffer resp_buf;
            response.pack(resp_buf);

            // Send back: [identity, empty_frame, response_bytes]
            zmq::message_t id_msg(identity.data(), identity.size());
            zmq::message_t empty_msg(0);
            zmq::message_t resp_msg(resp_buf.data(), resp_buf.size());

            router.send(id_msg, zmq::send_flags::sndmore);
            router.send(empty_msg, zmq::send_flags::sndmore);
            router.send(resp_msg, zmq::send_flags::none);
        } catch (const std::exception& e) {
            std::cerr << "Warning: failed to process command: " << e.what() << "\n";
            // Do not send response for malformed commands
        }
    }

    router.close();
}
