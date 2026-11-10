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
static void settle(void) {
    for(int i=0;i<40;i++){while(g_main_context_iteration(NULL,FALSE));g_usleep(10000);}
}
static void screen(App *app,const char *base,const char *suffix) {
    gchar *path=g_strdup_printf("%.*s-%s.png",(int)strlen(base)-4,base,suffix);
    snapshot(app,path);g_free(path);
}
static void visible_in_window(App *app,GtkWidget *widget) {
    graphene_rect_t bounds;g_assert_true(gtk_widget_compute_bounds(widget,GTK_WIDGET(app->window),&bounds));
    g_assert_cmpfloat(bounds.origin.y,>=,0);
    g_assert_cmpfloat(bounds.origin.y+bounds.size.height,<=,gtk_widget_get_height(GTK_WIDGET(app->window)));
    g_assert_cmpfloat(bounds.origin.x,>=,0);
    g_assert_cmpfloat(bounds.origin.x+bounds.size.width,<=,gtk_widget_get_width(GTK_WIDGET(app->window)));
}
static void layout_checks(App *app,const char *base,gboolean complete) {
    gtk_window_set_default_size(app->window,760,620);settle();
    g_assert_false(gtk_widget_get_visible(app->sidebar));
    visible_in_window(app,app->close);
    visible_in_window(app,complete?app->copy_connection:app->primary);
    screen(app,base,complete?"completion-small":"initial-small");
    gtk_window_set_default_size(app->window,500,620);
    GtkSettings *settings=gtk_settings_get_default();gchar *font=NULL;
    g_object_get(settings,"gtk-font-name",&font,NULL);int old_size=pango_font_description_get_size(pango_context_get_font_description(gtk_widget_get_pango_context(app->heading)));
    g_object_set(settings,"gtk-font-name","Sans 16",NULL);settle();
    int new_size=pango_font_description_get_size(pango_context_get_font_description(gtk_widget_get_pango_context(app->heading)));
    g_assert_cmpint(new_size,>,old_size);
    visible_in_window(app,app->close);visible_in_window(app,complete?app->copy_connection:app->primary);
    g_assert_true(gtk_widget_child_focus(GTK_WIDGET(app->window),GTK_DIR_TAB_FORWARD));
    screen(app,base,complete?"completion-scaled":"initial-scaled");
    g_object_set(settings,"gtk-font-name",font,NULL);g_free(font);
    gtk_window_set_default_size(app->window,1160,880);settle();
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
    g_assert_nonnull(gtk_image_get_paintable(GTK_IMAGE(app.icon)));
    GtkAccessibleRole role=gtk_accessible_get_accessible_role(GTK_ACCESSIBLE(app.full));
    g_assert_true(role==GTK_ACCESSIBLE_ROLE_RADIO || role==GTK_ACCESSIBLE_ROLE_CHECKBOX);
    gtk_check_button_set_active(GTK_CHECK_BUTTON(app.text),TRUE);
    g_assert_false(gtk_check_button_get_active(GTK_CHECK_BUTTON(app.full)));
    gtk_check_button_set_active(GTK_CHECK_BUTTON(full?app.full:app.text),TRUE);
    g_assert_true(gtk_widget_get_focusable(app.full));
    settle();
    g_assert_cmpint(gtk_widget_get_width(app.sidebar),<=,300);
    g_assert_cmpfloat(gtk_label_get_xalign(GTK_LABEL(app.step_numbers[0])),==,0.5);
    screen(&app,argv[4],"initial");layout_checks(&app,argv[4],FALSE);
    primary(NULL,&app);await_phase(&app,"bootstrapReview",FALSE,180);
    screen(&app,argv[4],"tools-review");
    /* Pre-download cancellation, then explicit retry and both plan-bound consents. */
    cancel(NULL,&app);await_phase(&app,"cancelled",TRUE,30);
    screen(&app,argv[4],"cancelled");
    gchar *data=g_build_filename(g_getenv("XDG_DATA_HOME"),"ComposerLint",NULL);
    g_assert_false(g_file_test(data,G_FILE_TEST_EXISTS));g_free(data);
    recheck(NULL,&app);await_phase(&app,"welcome",TRUE,180);
    primary(NULL,&app);await_phase(&app,"bootstrapReview",FALSE,180);
    primary(NULL,&app);await_phase(&app,"installReview",FALSE,900);
    screen(&app,argv[4],"installation-review");
    /* Reject dependency installation once; retained private tools/source must be
     * reverified by the production helper on the next attempt. */
    cancel(NULL,&app);await_phase(&app,"cancelled",TRUE,30);
    recheck(NULL,&app);await_phase(&app,"welcome",TRUE,180);
    gtk_check_button_set_active(GTK_CHECK_BUTTON(full?app.full:app.text),TRUE);
    primary(NULL,&app);await_phase(&app,"bootstrapReview",FALSE,180);
    primary(NULL,&app);await_phase(&app,"installReview",FALSE,900);
    g_assert_nonnull(app.prefix);
    primary(NULL,&app);g_assert_cmpuint(app.step,==,3);screen(&app,argv[4],"progress");await_phase(&app,"complete",TRUE,full?1800:300);
    g_assert_true(gtk_widget_get_sensitive(app.copy_connection));
    g_assert_nonnull(app.completion);
    g_assert_nonnull(strstr(app.completion,"18 tools"));
    g_assert_nonnull(strstr(app.completion,"export remains disabled"));
    g_assert_false(gtk_widget_get_visible(app.choices));
    g_assert_true(gtk_widget_get_visible(app.connection));
    g_assert_false(gtk_expander_get_expanded(GTK_EXPANDER(app.advanced)));
    visible_in_window(&app,app.copy_connection);
    g_assert_cmpint(gtk_widget_get_width(app.sidebar),<=,300);
    snapshot(&app,argv[4]);layout_checks(&app,argv[4],TRUE);
    gtk_expander_set_expanded(GTK_EXPANDER(app.advanced),TRUE);settle();
    GtkAdjustment *adjustment=gtk_scrolled_window_get_vadjustment(GTK_SCROLLED_WINDOW(app.content_scroll));
    g_assert_cmpfloat(gtk_adjustment_get_upper(adjustment),>,gtk_adjustment_get_page_size(adjustment));
    gtk_adjustment_set_value(adjustment,gtk_adjustment_get_upper(adjustment));settle();
    visible_in_window(&app,app.copy_connection);screen(&app,argv[4],"advanced-scrolled");
    gchar *real=g_strdup(gtk_label_get_text(GTK_LABEL(app.technical)));
    GString *long_path=g_string_new("Long path wrapping check: /home/");
    for(int i=0;i<40;i++)g_string_append(long_path,"long-directory-with-spaces 音楽/");
    gtk_label_set_text(GTK_LABEL(app.technical),long_path->str);g_string_free(long_path,TRUE);
    gtk_window_set_default_size(app.window,500,620);settle();
    visible_in_window(&app,app.copy_connection);screen(&app,argv[4],"long-path");
    gtk_label_set_text(GTK_LABEL(app.technical),real);g_free(real);
    gtk_expander_set_expanded(GTK_EXPANDER(app.advanced),FALSE);
    gtk_window_set_default_size(app.window,1160,880);settle();
    phase(&app,"error","Setup needs attention","Layout check: a connection was interrupted. Review the local log and recheck before retrying.");
    screen(&app,argv[4],"error-layout-check");
    phase(&app,"complete","ComposerLint is ready.","One final connection, then bring your score to the conversation.");
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
    json_object_set_boolean_member(report,"responsive_footer",TRUE);
    json_object_set_boolean_member(report,"stable_sidebar_and_step_alignment",TRUE);
    json_object_set_boolean_member(report,"font_scaling",TRUE);
    json_object_set_boolean_member(report,"long_path_scrolling",TRUE);
    json_object_set_boolean_member(report,"packaged_icon_loaded",TRUE);
    json_object_set_boolean_member(report,"keyboard_focus_and_choice_semantics",TRUE);
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
