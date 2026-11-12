// Evidence collector only. Never builds/installs an APK or grants approval.
import groovy.json.JsonOutput
import java.security.MessageDigest
import java.util.TreeMap
import org.gradle.api.artifacts.DependencyResolutionListener
import org.gradle.api.artifacts.ResolvableDependencies
import org.gradle.api.artifacts.component.ModuleComponentIdentifier
import org.gradle.api.artifacts.component.ModuleComponentSelector
import org.gradle.api.artifacts.result.ResolvedDependencyResult
import org.gradle.api.artifacts.verification.DependencyVerificationMode
import org.gradle.api.tasks.testing.Test

val collected = linkedMapOf<Pair<String, String>, MutableMap<String, Any>>()
val locations = linkedMapOf<Pair<String, String>, String>()
val components = mutableSetOf<String>()
val componentRoles = linkedMapOf<String, MutableSet<String>>()
val componentConfigurations = linkedMapOf<String, MutableSet<String>>()
val observedRoles = mutableSetOf<String>()
fun hashFile(file: File): String {
    val digest = MessageDigest.getInstance("SHA-256")
    file.inputStream().use { input ->
        val buffer = ByteArray(65536)
        while (true) { val n = input.read(buffer); if (n < 0) break; digest.update(buffer, 0, n) }
    }
    return digest.digest().joinToString("") { "%02x".format(it) }
}
fun rejectDynamic(version: String) {
    require(!Regex("[+\\[\\](),]|SNAPSHOT|latest|release", RegexOption.IGNORE_CASE).containsMatchIn(version)) {
        "Dynamic/changing dependency is outside the size contract: $version"
    }
}
fun roles(path: String): Set<String> = when {
    path.contains("classpath", ignoreCase = true) && !path.contains("compile", ignoreCase = true) && !path.contains("runtime", ignoreCase = true) -> setOf("buildscript", "plugins")
    path.contains("test", ignoreCase = true) -> setOf("tests")
    path.contains("runtime", ignoreCase = true) -> setOf("runtime")
    path.contains("compile", ignoreCase = true) -> setOf("compile")
    else -> emptySet()
}
fun record(component: String, file: File, roles: Set<String>, configuration: String) {
    require(file.isFile && !java.nio.file.Files.isSymbolicLink(file.toPath())) { "Missing/linked dependency bytes" }
    val key = component to file.name
    val digest = hashFile(file)
    synchronized(collected) {
        val existing = collected[key]
        require(existing == null || existing["sha256"] == digest) { "Ambiguous bytes for $key" }
        if (existing == null) {
            collected[key] = mutableMapOf("component" to component, "artifact" to file.name,
                "bytes" to file.length(), "sha256" to digest, "roles" to sortedSetOf<String>(),
                "configurations" to sortedSetOf<String>())
            locations[key] = file.canonicalPath
        }
        @Suppress("UNCHECKED_CAST")
        (collected[key]!!["roles"] as MutableSet<String>).addAll(roles)
        @Suppress("UNCHECKED_CAST")
        (collected[key]!!["configurations"] as MutableSet<String>).add(configuration)
        observedRoles.addAll(roles)
    }
}
// Registered before Settings/plugin resolution, not merely after projectsEvaluated.
gradle.addListener(object : DependencyResolutionListener {
    override fun beforeResolve(dependencies: ResolvableDependencies) {
        dependencies.dependencies.forEach { dependency -> dependency.version?.let { rejectDynamic(it) } }
    }
    override fun afterResolve(dependencies: ResolvableDependencies) {
        val role = roles(dependencies.path)
        dependencies.resolutionResult.allDependencies.forEach { dependency ->
            (dependency.requested as? ModuleComponentSelector)?.version?.let { rejectDynamic(it) }
        }
        dependencies.resolutionResult.allComponents.forEach { component ->
            (component.id as? ModuleComponentIdentifier)?.let { components.add("${it.group}:${it.module}:${it.version}") }
        }
        // Filter project components BEFORE artifact selection/materialization.
        // AGP project variants expose several artifact types; the legacy complete
        // artifact collection would resolve them before the per-item type check.
        val externalArtifacts = dependencies.artifactView {
            componentFilter { it is ModuleComponentIdentifier }
        }
        externalArtifacts.artifacts.artifacts.forEach { artifact ->
            (artifact.id.componentIdentifier as? ModuleComponentIdentifier)?.let {
                require(role.isNotEmpty()) { "Unclassified external resolution: ${dependencies.path}" }
                val id = "${it.group}:${it.module}:${it.version}"
                synchronized(collected) {
                    componentRoles.getOrPut(id) { sortedSetOf() }.addAll(role)
                    componentConfigurations.getOrPut(id) { sortedSetOf() }.add(dependencies.path)
                }
                // Artifact views may return derived AAR->classes.jar transforms.
                // Their raw Maven inputs are hashed from the fresh native cache below.
                if (!artifact.file.invariantSeparatorsPath.contains("/transforms/")) {
                    record(id, artifact.file, role, dependencies.path)
                }
            }
        }
    }
})
fun canonical(value: Any?): Any? = when (value) {
    is Map<*, *> -> TreeMap(value.entries.associate { it.key.toString() to canonical(it.value) })
    is Set<*> -> value.map { canonical(it) }.sortedBy { it.toString() }
    is List<*> -> value.map { canonical(it) }
    else -> value
}
// Selected M1 Robolectric classes explicitly use SDK 35. Route its runtime
// fetch through native Gradle verification instead of an unchecked ~/.m2 cache.
// This is test infrastructure only, not an APK implementation dependency.
gradle.beforeProject {
    if (path == ":app") {
        val sdk = configurations.create("sizeGateTestSdk") { isCanBeResolved = true; isCanBeConsumed = false }
        dependencies.add(sdk.name, "org.robolectric:android-all-instrumented:15-robolectric-13954326-i7")
        val sdkDir = layout.buildDirectory.dir("size-gate-test-sdk")
        val materialize = tasks.register("materializeSizeGateTestSdk") {
            inputs.files(sdk)
            outputs.dir(sdkDir)
            doLast {
                val jar = sdk.singleFile
                val dest = sdkDir.get().file(jar.name).asFile
                dest.parentFile.mkdirs()
                if (!dest.exists()) jar.copyTo(dest)
                require(hashFile(dest) == hashFile(jar)) { "Robolectric SDK copy differs from verified artifact" }
            }
        }
        tasks.withType(Test::class.java).configureEach {
            dependsOn(materialize)
            systemProperty("robolectric.offline", "true")
            systemProperty("robolectric.dependency.dir", sdkDir.get().asFile.absolutePath)
        }
    }
}
gradle.projectsEvaluated {
    val app = rootProject.findProject(":app") ?: rootProject
    app.tasks.register("writeDevelopmentSizeInputs") {
        group = "verification"
        description = "Read resolved external artifacts and descriptors; never approve an APK"
        doLast {
            rootProject.findProject(":app")?.configurations?.getByName("sizeGateTestSdk")?.resolve()
            rootProject.allprojects.forEach { project ->
                project.configurations.filter { it.isCanBeResolved &&
                    Regex("(developmentDebug|originalDebug|debugCompileClasspath|debugRuntimeClasspath)", RegexOption.IGNORE_CASE)
                        .containsMatchIn(it.name) }.forEach { configuration ->
                    configuration.incoming.artifactView { componentFilter { it is ModuleComponentIdentifier } }.artifacts.artifacts
                }
            }
            // Fresh Gradle user home: real downloaded Maven descriptors, including plugin markers.
            val cache = File(gradle.gradleUserHomeDir, "caches/modules-2/files-2.1")
            if (cache.isDirectory) cache.walkTopDown().filter { it.isFile }.forEach { file ->
                val parts = file.relativeTo(cache).invariantSeparatorsPath.split('/')
                require(parts.size == 5) { "Unknown descriptor cache layout" }
                val id = "${parts[0]}:${parts[1]}:${parts[2]}"
                // Parent POMs need not be dependency graph components. Every byte is
                // still required to match the pinned native verification metadata.
                if (file.extension == "pom" || file.extension == "module") {
                    record(id, file, setOf("descriptors"), "native-verification-descriptors")
                } else {
                    val roles = componentRoles[id] ?: error("Unclassified external cache input: $id/${file.name}")
                    val configs = componentConfigurations[id] ?: error("Missing native input origin")
                    configs.forEach { record(id, file, roles, it) }
                }
            }
            val verification = File(rootProject.projectDir, "gradle/verification-metadata.xml")
            require(verification.isFile || gradle.startParameter.writeDependencyVerifications.isNotEmpty()) { "Explicit verification metadata required" }
            val strict = gradle.startParameter.dependencyVerificationMode == DependencyVerificationMode.STRICT &&
                gradle.startParameter.writeDependencyVerifications.isEmpty()
            val expectedRoles = listOf("runtime", "compile", "tests", "buildscript", "plugins", "descriptors")
            val out = System.getenv("TVHOME_SIZE_INPUT_OUTPUT") ?: error("Explicit collector output required")
            val target = File(out)
            require(!target.exists() && !java.nio.file.Files.isSymbolicLink(target.toPath())) { "Collector output exists" }
            val result = mapOf("schema" to "tvhome.size-resolved-inputs/1", "strict_verification" to strict,
                "verification_metadata_sha256" to (if (verification.isFile) hashFile(verification) else null),
                "coverage" to expectedRoles.associateWith { it in observedRoles },
                "artifacts" to collected.toSortedMap(compareBy<Pair<String, String>> { it.first }.thenBy { it.second }).values.toList(),
                "paths" to locations.toSortedMap(compareBy<Pair<String, String>> { it.first }.thenBy { it.second }).map {
                    mapOf("component" to it.key.first, "artifact" to it.key.second, "path" to it.value) })
            val payload = JsonOutput.toJson(canonical(result)) + "\n"
            java.nio.file.Files.writeString(target.toPath(), payload, java.nio.file.StandardOpenOption.CREATE_NEW)
            println("Size input evidence recorded; coverage=$observedRoles; strict=$strict; no approval")
        }
    }
}
