# Keep kotlinx.serialization
-keepattributes *Annotation*, InnerClasses
-dontnote kotlinx.serialization.AnnotationsKt

-keepclassmembers class kotlinx.serialization.json.** {
    *** Companion;
}
-keepclasseswithmembers class kotlinx.serialization.json.** {
    kotlinx.serialization.KSerializer serializer(...);
}
-keep,includedescriptorclasses class com.coybot.drone.**$$serializer { *; }
-keepclassmembers class com.coybot.drone.** {
    *** Companion;
}
-keepclasseswithmembers class com.coybot.drone.** {
    kotlinx.serialization.KSerializer serializer(...);
}
