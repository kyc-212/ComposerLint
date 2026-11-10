/* Native CI harness only. This executable is never included in the setup ZIP.
 * Reuses the production GTK callbacks and real helper/backend; no export access. */
#define main composerlint_frontend_main
#include "setup.c"
#undef main

#include <stdlib.h>

static void await_phase(App *app,const char *expected,gboolean idle,gint seconds) {
    gint64 deadline=g_get_monotonic_time()+(gint64)seconds*G_USEC_PER_SEC;
    while(g_get_monotonic_time()<deadline) {
        while(g_main_context_iteration(NULL,FALSE));
        if(app->phase && g_str_equal(app->phase,"error"))
            g_error("GUI refused %s: %s",expected,gtk_label_get_text(GTK_LABEL(app->message)));
        if(app->phase && g_str_equal(app->phase,expected) && (!idle || !app->job))return;
        g_usleep(10000);
    }
    g_error("GUI deadline waiting for %s; last phase %s",expected,app->phase?app->phase:"none");
}
static void snapshot(App *app,const char *path) {
    /* Authored setup screens only; no score or user configuration is opened. */
    for(int i=0;i<20;i++){while(g_main_context_iteration(NULL,FALSE));g_usleep(10000);}
    GtkWidget *widget=GTK_WIDGET(app->window);
    GdkPaintable *paintable=gtk_widget_paintable_new(widget);
    GtkSnapshot *view=gtk_snapshot_new();
    gdk_paintable_snapshot(paintable,GDK_SNAPSHOT(view),gtk_widget_get_width(widget),gtk_widget_get_height(widget));
    GskRenderNode *node=gtk_snapshot_free_to_node(view);g_assert_nonnull(node);
    GdkTexture *texture=gsk_renderer_render_texture(gtk_native_get_renderer(GTK_NATIVE(app->window)),node,NULL);
    g_assert_true(gdk_texture_save_to_png(texture,path));
    g_object_unref(texture);gsk_render_node_unref(node);g_object_unref(paintable);
}
int main(int argc,char **argv) {
    if(argc!=5)return 2; /* extracted app, full|text, result JSON, screenshot */
    g_assert_true(g_str_equal(argv[2],"full") || g_str_equal(argv[2],"text"));
    gboolean full=g_str_equal(argv[2],"full");
    gtk_init();
    App app={0};app.root=g_canonicalize_filename(argv[1],NULL);
    GtkApplication *application=gtk_application_new("org.composerlint.ci",G_APPLICATION_NON_UNIQUE);
    GError *error=NULL;g_assert_true(g_application_register(G_APPLICATION(application),NULL,&error));
    activate(application,&app);
    await_phase(&app,"welcome",TRUE,180);
    g_assert_true(app.supported);g_assert_true(app.full_available);
    g_assert_false(boolean(app.diagnostic,"linux_export_available"));
    gtk_check_button_set_active(GTK_CHECK_BUTTON(full?app.full:app.text),TRUE);
    primary(NULL,&app);await_phase(&app,"bootstrapReview",FALSE,180);
    /* Pre-download cancellation, then explicit retry and both plan-bound consents. */
    cancel(NULL,&app);await_phase(&app,"cancelled",TRUE,30);
    gchar *data=g_build_filename(g_getenv("XDG_DATA_HOME"),"ComposerLint",NULL);
    g_assert_false(g_file_test(data,G_FILE_TEST_EXISTS));g_free(data);
    recheck(NULL,&app);await_phase(&app,"welcome",TRUE,180);
    primary(NULL,&app);await_phase(&app,"bootstrapReview",FALSE,180);
    primary(NULL,&app);await_phase(&app,"installReview",FALSE,900);
    /* Reject dependency installation once; retained private tools/source must be
     * reverified by the production helper on the next attempt. */
    cancel(NULL,&app);await_phase(&app,"cancelled",TRUE,30);
    recheck(NULL,&app);await_phase(&app,"welcome",TRUE,180);
    gtk_check_button_set_active(GTK_CHECK_BUTTON(full?app.full:app.text),TRUE);
    primary(NULL,&app);await_phase(&app,"bootstrapReview",FALSE,180);
    primary(NULL,&app);await_phase(&app,"installReview",FALSE,900);
    g_assert_nonnull(app.prefix);
    primary(NULL,&app);await_phase(&app,"complete",TRUE,full?1800:300);
    g_assert_true(gtk_widget_get_sensitive(app.copy_connection));
    g_assert_nonnull(app.completion);
    g_assert_nonnull(strstr(app.completion,"18 tools"));
    g_assert_nonnull(strstr(app.completion,"export remains disabled"));
    snapshot(&app,argv[4]);
    JsonObject *report=json_object_new();
    json_object_set_string_member(report,"mode",argv[2]);
    json_object_set_string_member(report,"prefix",app.prefix);
    gchar *source=g_build_filename(g_getenv("XDG_DATA_HOME"),"ComposerLint","setup","sources",MANIFEST_SHA256,NULL);
    g_assert_true(g_file_test(source,G_FILE_TEST_IS_DIR));
    json_object_set_string_member(report,"source",source);g_free(source);
    json_object_set_string_member(report,"instructions",app.completion);
    json_object_set_boolean_member(report,"bootstrap_cancel",TRUE);
    json_object_set_boolean_member(report,"dependency_cancel_retry",TRUE);
    json_object_set_boolean_member(report,"two_plan_bound_consents",TRUE);
    json_object_set_boolean_member(report,"gtk_protocol_complete",TRUE);
    json_object_set_boolean_member(report,"desktop_acceptance",FALSE);
    JsonNode *node=json_node_new(JSON_NODE_OBJECT);json_node_take_object(node,report);
    JsonGenerator *generator=json_generator_new();json_generator_set_root(generator,node);
    g_assert_true(json_generator_to_file(generator,argv[3],&error));
    g_object_unref(generator);json_node_free(node);
    if(app.ticker){g_source_remove(app.ticker);app.ticker=0;}
    gtk_window_destroy(app.window);g_object_unref(application);
    g_free(app.root);g_free(app.phase);g_free(app.plan_id);g_free(app.completion);g_free(app.prefix);
    g_clear_pointer(&app.diagnostic,json_object_unref);
    return 0;
}
