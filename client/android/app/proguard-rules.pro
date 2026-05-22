# Keep kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt

-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.astral.drone.**$$serializer { *; }
-keepclassmembers class com.astral.drone.** {
    *** Companion;
}
-keepclasseswithmembers class com.astral.drone.** {
    kotlinx.serialization.KSerializer serializer(...);
}
