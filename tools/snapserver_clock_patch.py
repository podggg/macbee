"""Exact, opt-in patch recipe for the pinned Snapcast 0.35.0 pipe reader.
release_backend serializes these replacements alongside both original archives.
No sample inspection, exported audio, network endpoint, or persistent worker.
V2 can overwrite a bounded number of obsolete input chunks with silence.
V3 also bridges an outstanding pipe read on an independent presentation timer.
"""
PATH = 'server/streamreader/asio_stream.hpp'

def specs():
    includes = """#include <array>
#include <functional>
#include <cerrno>
#include <climits>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <sys/un.h>
"""
    members = """    // One dedicated, inherited AF_UNIX datagram peer. Numeric records only.
    int macbee_fd_ = -1;
    uint64_t macbee_frames_ = 0;
    // V3's presentation timer runs on the same strand as the pending pipe read.
    // Its immutable zero chunk is separate from even a partially filled read.
    bool macbee_slots_ = false, macbee_bridge_ = false, macbee_waiting_ = false;
    uint64_t macbee_input_slot_ = 0, macbee_last_input_slot_ = 0, macbee_presented_slot_ = 0;
    void macbeeSlotProgress()
    {
        macbee_frames_ += chunk_->getFrameCount();
        macbee_consumed_at_ = std::chrono::steady_clock::now();
        macbeeFlush();
    }
    void macbeeAdvanceSlot(std::function<void()> deliver)
    {
        if (!active_.load() || !stream_) return;
        if (macbee_input_slot_ > macbee_presented_slot_ + 1)
        {
            const auto now = std::chrono::steady_clock::now();
            if (tvEncodedChunk_ + 500ms - now < 120ms) { macbeeStopStale(); return; }
            this->chunkRead(*macbee_zero_);
            ++macbee_presented_slot_;
            macbee_inserted_ += macbee_zero_->getFrameCount();
            nextTick_ += macbee_zero_->duration<std::chrono::nanoseconds>();
            boost::asio::post(strand_, [this, self = shared_from_this(), deliver = std::move(deliver)]() mutable {
                macbeeAdvanceSlot(std::move(deliver));
            });
            return;
        }
        deliver();
    }
    uint64_t macbee_read_generation_ = 0;
    std::array<std::chrono::steady_clock::time_point,4> macbee_gap_starts_{};
    size_t macbee_gap_index_ = 0;
    std::unique_ptr<msg::PcmChunk> macbee_zero_;
    std::unique_ptr<boost::asio::steady_timer> macbee_bridge_timer_;
    void macbeeBridge(uint64_t generation, unsigned slots)
    {
        // Preserve short jitter in the original media grid. Only once the
        // common 250ms recovery boundary is crossed fill the missing slots,
        // one strand callback at a time; thereafter follow the live grid.
        // An autonomous fill cannot beat the source decision by a scheduling
        // quantum. A sender-declared skipped slot does not need this guard.
        const auto autonomous = std::max(nextTick_ + 250ms, macbee_consumed_at_ + 250ms +
                                         2 * chunk_->duration<std::chrono::nanoseconds>());
        macbee_bridge_timer_->expires_at(slots ? nextTick_ : autonomous);
        macbee_bridge_timer_->async_wait([this, self = shared_from_this(), generation, slots](const boost::system::error_code& ec) {
            if (ec || !active_.load() || !macbee_waiting_ || generation != macbee_read_generation_) return;
            const auto now = std::chrono::steady_clock::now();
            if (slots == 0 && macbee_slots_)
            {
                const auto oldest = macbee_gap_starts_[macbee_gap_index_];
                if (oldest.time_since_epoch().count() && now - oldest < 10s)
                {
                    macbeeStopStale(3);
                    return;
                }
                macbee_gap_starts_[macbee_gap_index_] = now;
                macbee_gap_index_ = (macbee_gap_index_ + 1) % macbee_gap_starts_.size();
            }
            if (now - macbee_consumed_at_ >= 720ms || slots >= 34 || tvEncodedChunk_ + 500ms - now < 120ms)
            {
                macbeeStopStale();
                return;
            }
            this->chunkRead(*macbee_zero_);
            if (macbee_slots_) ++macbee_presented_slot_;
            macbee_inserted_ += macbee_zero_->getFrameCount();
            nextTick_ += macbee_zero_->duration<std::chrono::nanoseconds>();
            // One slot per callback; no recursion without yielding or fake read credit.
            macbeeBridge(generation, slots + 1);
        });
    }
    bool macbee_recover_ = false, macbee_filled_episode_ = false;
    uint64_t macbee_inserted_ = 0;
    std::chrono::nanoseconds macbee_previous_late_{0};
    void macbeeStopStale(uint64_t reason = 2)
    {
        macbeeSend(reason, std::chrono::steady_clock::now());
        macbee_timer_->cancel();
        macbee_waiting_ = false;
        if (macbee_bridge_timer_) macbee_bridge_timer_->cancel();
        PcmStream::stop();
        disconnect();
    }
    void macbeeFill(unsigned remaining)
    {
        if (!active_.load() || !stream_) return;
        // Input buffer was already silenced, and there is no concurrent stdin read.
        // Yield after EACH placeholder. Synthetic output is never input progress.
        auto now = std::chrono::steady_clock::now();
        if (tvEncodedChunk_ + 500ms - now < 120ms) { macbeeStopStale(); return; }
        if (remaining && nextTick_ <= now)
        {
            this->chunkRead(*chunk_);
            macbee_inserted_ += chunk_->getFrameCount();
            nextTick_ += chunk_->duration<std::chrono::nanoseconds>();
            boost::asio::post(strand_, [this, self = shared_from_this(), remaining] { macbeeFill(remaining - 1); });
            return;
        }
        do_read();
    }
    uint64_t macbee_concealed_ = 0, macbee_episode_frames_ = 0;
    std::chrono::steady_clock::time_point macbee_healthy_since_{};

    uint64_t macbee_sent_frames_ = 0;
    std::chrono::steady_clock::time_point macbee_consumed_at_{}, macbee_last_attempt_{};
    std::unique_ptr<boost::asio::steady_timer> macbee_timer_;
    bool macbee_timer_pending_ = false;
    bool macbeeSend(uint64_t state, std::chrono::steady_clock::time_point when)
    {
        uint64_t wire[5] = {state, macbee_frames_, static_cast<uint64_t>(
            std::chrono::duration_cast<std::chrono::nanoseconds>(when.time_since_epoch()).count()), macbee_concealed_, macbee_inserted_};
        const size_t wire_size = macbee_recover_ ? sizeof(wire) : 3 * sizeof(uint64_t);
        int flags = MSG_DONTWAIT;
#ifdef MSG_NOSIGNAL
        flags |= MSG_NOSIGNAL;
#endif
        return ::send(macbee_fd_, wire, wire_size, flags) == static_cast<ssize_t>(wire_size);
    }
    void macbeeFlush()
    {
        if (macbee_sent_frames_ == macbee_frames_) return;
        auto now = std::chrono::steady_clock::now();
        if (now - macbee_last_attempt_ >= 40ms)
        {
            macbee_last_attempt_ = now;
            if (macbeeSend(1, macbee_consumed_at_))
            {
                macbee_sent_frames_ = macbee_frames_;
                return;
            }
        }
        if (macbee_timer_pending_) return;
        macbee_timer_pending_ = true;
        // Flush a final coalesced record even when the reader has no next chunk.
        // Always report the original consume timestamp, never the timer's wake time.
        macbee_timer_->expires_at(macbee_last_attempt_ + 40ms);
        macbee_timer_->async_wait([this, self = shared_from_this()](const boost::system::error_code& ec) {
            macbee_timer_pending_ = false;
            if (!ec && active_.load()) macbeeFlush();
        });
    }
"""
    setup = """    const auto clock_mode = uri_.getQuery("macbee_clock", "");
    macbee_slots_ = clock_mode == "v3-slots";
    macbee_bridge_ = clock_mode == "v3" || macbee_slots_;
    macbee_recover_ = clock_mode == "v2" || macbee_bridge_;
    if (clock_mode == "v1" || macbee_recover_)
    {
        if (uri_.scheme != "pipe" || uri_.path != "/dev/stdin" || chunk_ms_ != 20)
            throw std::runtime_error("Unsupported Macbee reader clock configuration");
        if (macbee_recover_ && (server_settings_.stream.bufferMs != 500 || sampleFormat_.rate() != 48000 ||
            sampleFormat_.bits() != 16 || sampleFormat_.channels() != 2 || chunk_->payloadSize != 3840))
            throw std::runtime_error("Unsupported bounded recovery format");
        const char* value = std::getenv("MACBEE_READER_FD");
        char* end = nullptr;
        errno = 0;
        long fd = value ? std::strtol(value, &end, 10) : -1;
        sockaddr_storage peer{};
        socklen_t size = sizeof(peer);
        int kind = 0;
        socklen_t kind_size = sizeof(kind);
        if (!value || end == value || *end || errno || fd < 3 || fd > INT_MAX ||
            ::getpeername(static_cast<int>(fd), reinterpret_cast<sockaddr*>(&peer), &size) ||
            peer.ss_family != AF_UNIX ||
            ::getsockopt(static_cast<int>(fd), SOL_SOCKET, SO_TYPE, &kind, &kind_size) || kind != SOCK_DGRAM)
            throw std::runtime_error("Macbee reader telemetry is unavailable");
#ifdef SO_NOSIGPIPE
        int one = 1;
        if (::setsockopt(static_cast<int>(fd), SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one)))
            throw std::runtime_error("Macbee reader telemetry is unavailable");
#endif
        if (macbee_bridge_)
        {
            macbee_zero_ = std::make_unique<msg::PcmChunk>(sampleFormat_, chunk_ms_);
            std::memset(macbee_zero_->payload, 0, macbee_zero_->payloadSize);
            macbee_bridge_timer_ = std::make_unique<boost::asio::steady_timer>(strand_);
        }
        macbee_fd_ = static_cast<int>(fd);
        macbee_timer_ = std::make_unique<boost::asio::steady_timer>(strand_);
    }
"""
    stale = """        // Handle only complete chunks after successful input. This is not a
        // heartbeat and never sends stale captured PCM. A bounded placeholder
        // keeps the exact original media timestamp/sequence on every receiver.
        unsigned macbee_fill = 0;
        if (macbee_fd_ >= 0 && !first_ && !macbee_slots_)
        {
            const auto now = std::chrono::steady_clock::now();
            const auto late = now - nextTick_;
            if (late >= 250ms)
            {
                const auto count = chunk_->getFrameCount();
                // Preserve >= 120ms of the configured 500ms playout lead.
                // Five 20ms placeholders per episode; outstanding pipe credit is six.
                if (!macbee_recover_ || tvEncodedChunk_ + 500ms - now < 120ms ||
                    macbee_episode_frames_ + count > 4800)
                {
                    macbeeStopStale();
                    return;
                }
                // If genuine data is back but two late reads are NOT paying down
                // debt and the kernel has no full next chunk, there is nothing
                // to catch up with. Fill only the already missing presentation
                // slots in one bounded episode; never shift/duplicate a timestamp.
                int available = 0;
                if (!macbee_filled_episode_ && macbee_episode_frames_ &&
                    late + 10ms >= macbee_previous_late_ &&
                    ::ioctl(stream_->native_handle(), FIONREAD, &available) == 0 &&
                    available >= 0 && static_cast<uint32_t>(available) < chunk_->payloadSize)
                {
                    macbee_fill = 17;
                    macbee_filled_episode_ = true;
                }
                macbee_previous_late_ = std::chrono::duration_cast<std::chrono::nanoseconds>(late);
                std::memset(chunk_->payload, 0, chunk_->payloadSize);
                macbee_concealed_ += count;
                macbee_episode_frames_ += count;
                macbee_healthy_since_ = {};
            }
            else if (macbee_episode_frames_)
            {
                // Re-arm only after a full second of on-time reads, not after
                // one good packet among repeatedly late packets.
                if (late > 40ms) macbee_healthy_since_ = {};
                else if (macbee_healthy_since_.time_since_epoch().count() == 0)
                    macbee_healthy_since_ = now;
                else if (now - macbee_healthy_since_ >= 1s)
                {
                    macbee_episode_frames_ = 0;
                    macbee_previous_late_ = std::chrono::nanoseconds::zero();
                    macbee_filled_episode_ = false;
                }
            }
        }

"""
    progress = """        if (macbee_slots_) ++macbee_presented_slot_;
        if (macbee_fd_ >= 0 && !macbee_slots_)
        {
            macbee_frames_ += chunk_->getFrameCount();
            macbee_consumed_at_ = std::chrono::steady_clock::now();
            macbeeFlush();
        }
"""
    bounded = """            if (macbee_fd_ >= 0)
            {
                // Only one read in flight, and always yield between overdue chunks.
                // Preserve both the read clock and the PCM timestamp sequence.
                boost::asio::post(strand_, [this, self = shared_from_this()] {
                    if (active_.load() && stream_) do_read();
                });
                return;
            }
"""
    pairs = [('// standard headers', '// standard headers\n'+includes),
             ('    /// Marker: next chunk is the first chunk',members+'    /// Marker: next chunk is the first chunk'),
             ('    buffer_ms_ = 50;',setup+'    buffer_ms_ = 50;'),
             ('    read_timer_.cancel();','    read_timer_.cancel();\n    if (macbee_timer_) macbee_timer_->cancel();'),
             ('        if (isSilent(*chunk_))',stale+'        if (isSilent(*chunk_))'),
             ('        // Synchronize read to chunk_ms_',
              '        if (macbee_fill) { boost::asio::post(strand_, [this, self = shared_from_this(), macbee_fill] { macbeeFill(macbee_fill); }); return; }\n        // Synchronize read to chunk_ms_'),
             ('        chunkRead(*chunk_);','        chunkRead(*chunk_);\n'+progress),
             ('            resync(std::chrono::duration_cast<std::chrono::nanoseconds>(currentTick - nextTick_));',
              bounded+'            resync(std::chrono::duration_cast<std::chrono::nanoseconds>(currentTick - nextTick_));')]
    pairs.extend([
        ('    // Reset the silence timer', """    if (macbee_bridge_ && !first_)
    {
        macbee_waiting_ = true;
        macbeeBridge(++macbee_read_generation_, 0);
    }
    // Reset the silence timer"""),
        ('        state_timer_.cancel();', """        state_timer_.cancel();
        if (macbee_bridge_)
        {
            macbee_waiting_ = false;
            ++macbee_read_generation_;
            macbee_bridge_timer_->cancel();
            if (!active_.load()) return;
        }"""),
        ('    if (macbee_timer_) macbee_timer_->cancel();', """    if (macbee_timer_) macbee_timer_->cancel();
    macbee_waiting_ = false;
    if (macbee_bridge_timer_) macbee_bridge_timer_->cancel();""")
    ])
    pairs.extend([
        ('    boost::asio::async_read(*stream_, boost::asio::buffer(chunk_->payload, chunk_->payloadSize),',
         """    std::array<boost::asio::mutable_buffer, 2> buffers = {
        boost::asio::buffer(&macbee_input_slot_, macbee_slots_ ? sizeof(macbee_input_slot_) : 0U),
        boost::asio::buffer(chunk_->payload, chunk_->payloadSize)};
    boost::asio::async_read(*stream_, buffers,"""),
        ('        if (isSilent(*chunk_))', """        if (macbee_slots_)
        {
            if (length != chunk_->payloadSize + sizeof(macbee_input_slot_) ||
                macbee_input_slot_ <= macbee_last_input_slot_ ||
                macbee_input_slot_ - macbee_last_input_slot_ > 35 ||
                (first_ && macbee_input_slot_ != 1))
            {
                macbeeStopStale();
                return;
            }
            macbee_last_input_slot_ = macbee_input_slot_;
            if (macbee_input_slot_ <= macbee_presented_slot_)
            {
                // The clock already presented this slot, possibly while the
                // sender was waking. Consume, but never append a late copy.
                macbee_concealed_ += chunk_->getFrameCount();
                macbeeSlotProgress();
                boost::asio::post(strand_, [this, self = shared_from_this()] {
                    if (active_.load() && stream_) do_read();
                });
                return;
            }
            if (!first_ && tvEncodedChunk_ + 500ms - std::chrono::steady_clock::now() < 120ms)
            {
                macbeeStopStale();
                return;
            }
            macbeeSlotProgress();  // Real input completed, before any synthetic gap fill.
        }
        auto deliver = [this, self = shared_from_this(), macbee_fill]() {
        if (isSilent(*chunk_))"""),
        ('            first_ = true;\n            do_read();\n        }\n    });',
         """            first_ = true;
            do_read();
        }
        };
        if (macbee_slots_ && macbee_input_slot_ > macbee_presented_slot_ + 1)
            macbeeAdvanceSlot(std::move(deliver));
        else deliver();
    });""")])
    return [(PATH, before, after) for before, after in pairs]
