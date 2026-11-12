package dev.glasslauncher.addons

import dev.glasslauncher.system.RuntimeSafety

import android.content.ComponentName
import android.content.ContentProviderClient
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.IBinder
import android.os.Parcel
import dev.glasslauncher.addon.AddonApi
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.async
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import java.util.concurrent.ConcurrentHashMap
import kotlin.coroutines.cancellation.CancellationException

/**
 * Pure core of a call: at most one call in flight per add-on [key], a bounded wait, and every failure (connect error,
 * dead object, exception, timeout) is a null. Connecting AND the transaction run on [scope], outside the caller, because
 * neither can be interrupted (acquiring a provider is a blocking call too); the wait is what [timeoutMs] bounds. The
 * add-on stays "in flight" until it really returns, and [release] always runs (a provider client is always closed).
 */
internal class AddonCall(private val timeoutMs: Long, private val scope: CoroutineScope) {
    private val inFlight = ConcurrentHashMap.newKeySet<String>()

    suspend fun <B> run(key: String, connect: suspend () -> B, transact: (B) -> String?, release: (B) -> Unit = {}): String? {
        if (!inFlight.add(key)) return null
        val job = try {
            scope.async {
                var b: B? = null
                var connected = false
                try {
                    b = connect(); connected = true
                    transact(b)
                } catch (e: CancellationException) { throw e } catch (_: Exception) { null } finally {
                    @Suppress("UNCHECKED_CAST") if (connected) try { release(b as B) } catch (_: Exception) {}
                    inFlight.remove(key)
                }
            }
        } catch (e: Exception) { inFlight.remove(key); return null }
        return try {
            withTimeoutOrNull(timeoutMs) { job.await() }
        } catch (e: CancellationException) {
            // The caller gave up (not a timeout): stop connecting too, and wait briefly for the slot to be given back, so the next
            // call is not refused by a call nobody is waiting for. A call stuck in the add-on (not cancellable) is not waited for.
            job.cancel()
            withContext(NonCancellable) { withTimeoutOrNull(200) { job.join() } }
            throw e // never swallowed, not an add-on failure
        } catch (_: Exception) {
            null
        }
    }
}

/** Bind on demand, one transaction, unbind. Everything runs off Main; the main thread only sees the connect callbacks. */
@OptIn(ExperimentalCoroutinesApi::class)
internal object AddonClient {
    const val TIMEOUT_MS = 2_000L
    private val core = AddonCall(TIMEOUT_MS, CoroutineScope(SupervisorJob() + Dispatchers.IO.limitedParallelism(4)))

    /** The add-on's reply string, or null on timeout, bind failure or error. */
    suspend fun call(context: Context, info: AddonInfo, code: Int, request: String): String? = withContext(Dispatchers.IO) {
        if (RuntimeSafety.isolated) return@withContext null
        val connected = CompletableDeferred<IBinder>()
        val conn = object : ServiceConnection {
            override fun onServiceConnected(name: ComponentName, service: IBinder) { connected.complete(service) }
            override fun onServiceDisconnected(name: ComponentName) { connected.completeExceptionally(IllegalStateException("disconnected")) }
            override fun onBindingDied(name: ComponentName) { connected.completeExceptionally(IllegalStateException("binding died")) }
            override fun onNullBinding(name: ComponentName) { connected.completeExceptionally(IllegalStateException("null binding")) }
        }
        val intent = Intent(AddonApi.ACTION).setComponent(ComponentName(info.pkg, info.service))
        try {
            core.run(info.pkg, connect = {
                check(context.bindService(intent, conn, Context.BIND_AUTO_CREATE)) { "bind refused" }
                connected.await()
            }, transact = { transact(it, code, request) })
        } finally {
            try { context.unbindService(conn) } catch (_: IllegalArgumentException) {}
            connected.cancel() // a connect still waiting must not keep the add-on "in flight"
        }
    }

    /** One ContentProvider.call to the add-on's provider (Top Shelf, Home row, Settings). Same budget as a bind: 2 s, one call in flight per add-on. */
    suspend fun provider(context: Context, info: AddonInfo, method: String, request: String): String? = withContext(Dispatchers.IO) {
        if (RuntimeSafety.isolated) return@withContext null
        val authority = info.authority ?: return@withContext null
        core.run(info.pkg, connect = {
            // The authority must still belong to the add-on's own package, checked right before the acquire.
            check(context.packageManager.resolveContentProvider(authority, 0)?.packageName == info.pkg) { "provider is not the add-on's" }
            // Unstable: if the add-on's process dies mid-call, Glass is not taken down with it.
            context.contentResolver.acquireUnstableContentProviderClient(authority) ?: error("no provider")
        }, transact = { c -> c.call(method, request, null)?.getString(AddonApi.KEY_JSON) }, release = { it.close() })
    }

    private fun transact(binder: IBinder, code: Int, request: String): String? {
        val data = Parcel.obtain()
        val reply = Parcel.obtain()
        return try {
            data.writeString(request)
            if (binder.transact(code, data, reply, 0)) reply.readString() else null
        } finally {
            data.recycle(); reply.recycle()
        }
    }
}
