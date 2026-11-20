"""Exact Darwin worker scheduling edits for the pinned Snapcast backend."""

WORKER_QOS = r'''#if defined(__APPLE__)
#include <pthread.h>
#include <sys/qos.h>

static void macbeeRaisePlaybackWorkerQos(const char* role)
{
    const auto before = qos_class_self();
    // Never lower a worker to user-initiated from user-interactive.
    const int error = before < QOS_CLASS_USER_INITIATED
                          ? pthread_set_qos_class_self_np(QOS_CLASS_USER_INITIATED, 0)
                          : 0;
    const auto after = qos_class_self();
    LOG(DEBUG, "MacbeeQoS") << "MACBEE_WORKER_QOS " << role << " "
                            << static_cast<int>(before) << " " << static_cast<int>(after)
                            << " " << error << "\n";
}
#endif
'''


def specs():
    server = 'server/snapserver.cpp'
    core = 'client/player/coreaudio_player.cpp'
    file = 'client/player/file_player.cpp'

    def includes(path, anchor):
        return path, anchor, anchor + '\n' + WORKER_QOS

    return [
        includes(server, '#include "common/aixlog.hpp"\n'),
        (server, '''        for (int n = 0; n < settings.server.threads; ++n)
            threads.emplace_back([&] { io_context.run(); });

        io_context.run();''', '''        // The private pipe stream's strand can dispatch on any of these workers.
        auto runIo = [&] {
#if defined(__APPLE__)
            macbeeRaisePlaybackWorkerQos("server-io");
#endif
            io_context.run();
        };
        for (int n = 0; n < settings.server.threads; ++n)
            threads.emplace_back(runIo);

        runIo();'''),
        includes(core, '#include "common/aixlog.hpp"\n'),
        (core, 'void CoreAudioPlayer::worker()\n{\n', '''void CoreAudioPlayer::worker()
{
#if defined(__APPLE__)
    // AudioQueue delivers its application callbacks to this worker's CFRunLoop.
    // Do not change policy inside an OS real-time audio callback.
    macbeeRaisePlaybackWorkerQos("client-coreaudio");
#endif
'''),
        includes(file, '#include "common/aixlog.hpp"\n'),
        (file, 'void FilePlayer::requestAudio()\n{\n', '''void FilePlayer::requestAudio()
{
#if defined(__APPLE__)
    // Headless file output uses the event loop, rather than an AudioQueue.
    // Touch each actual worker only once, on its first file-player request.
    static thread_local const bool qosApplied = [] {
        macbeeRaisePlaybackWorkerQos("client-file");
        return true;
    }();
    (void)qosApplied;
#endif
'''),
    ]
