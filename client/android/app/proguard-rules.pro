# Keep kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt

-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.presidio.drone.**$$serializer { *; }
-keepclassmembers class com.presidio.drone.** {
    *** Companion;
}
-keepclasseswithmembers class com.presidio.drone.** {
    kotlinx.serialization.KSerializer serializer(...);
}
