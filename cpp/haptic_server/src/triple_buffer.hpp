#pragma once
#include <atomic>
#include <cstdint>

/// Lock-free triple buffer for single-writer / single-reader data sharing.
/// Writer never blocks; reader always gets the latest published snapshot.
template <typename T>
class TripleBuffer {
public:
    TripleBuffer()
        : write_idx_{0}
        , read_idx_{2}
        , shared_{static_cast<uint8_t>((1 << 1) | 0)}  // dirty=1, no new data
    {}

    /// Return a mutable reference to the current write slot.
    T& write_buffer() { return buf_[write_idx_]; }

    /// Make the current write slot available to the reader.
    void publish() {
        uint8_t new_val = static_cast<uint8_t>((write_idx_ << 1) | 1);
        uint8_t old_val = shared_.exchange(new_val, std::memory_order_acq_rel);
        write_idx_ = (old_val >> 1) & 0x3;
    }

    /// Swap to the latest published slot. Returns true if new data was available.
    bool swap_read_buffer() {
        uint8_t new_val = static_cast<uint8_t>((read_idx_ << 1) | 0);
        uint8_t old_val = shared_.exchange(new_val, std::memory_order_acq_rel);
        if (old_val & 1) {
            read_idx_ = (old_val >> 1) & 0x3;
            return true;
        }
        return false;
    }

    /// Return a const reference to the current read slot.
    const T& read_buffer() const { return buf_[read_idx_]; }

private:
    T buf_[3]{};
    uint8_t write_idx_;
    uint8_t read_idx_;
    std::atomic<uint8_t> shared_;
};
