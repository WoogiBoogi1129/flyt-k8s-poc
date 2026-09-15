package controller

import (
    "context"
    "fmt"

    api "github.com/WoogiBoogi1129/flyt-k8s-poc/controllers/flyt/api/v1alpha1"
    appsv1 "k8s.io/api/apps/v1"
    corev1 "k8s.io/api/core/v1"
    netv1 "k8s.io/api/networking/v1"
    "sigs.k8s.io/controller-runtime/pkg/client"
)
func (b *Base) pods(ctx context.Context,owner client.Object,role string)([]corev1.Pod,error){
    list:=&corev1.PodList{};err:=b.Reader.List(ctx,list,client.InNamespace(owner.GetNamespace()),client.MatchingLabels(labels(owner,role)));return list.Items,err
}
func active(p corev1.Pod)bool{return p.Status.Phase!=corev1.PodSucceeded&&p.Status.Phase!=corev1.PodFailed}
func podReady(p corev1.Pod)bool{
    if !p.DeletionTimestamp.IsZero()||p.Status.Phase!=corev1.PodRunning{return false}
    for _,c:=range p.Status.Conditions{if c.Type==corev1.PodReady&&c.Status==corev1.ConditionTrue{return true}};return false
}
// Delete deployments first, wait for all matching Pod objects to disappear, then
// remove policy/service/config. Never force-delete or infer GPU release from a timer.
func (b *Base) stopResources(ctx context.Context,owner client.Object,role string)(bool,error){
    selectors:=[]client.ListOption{client.InNamespace(owner.GetNamespace()),client.MatchingLabels(labels(owner,role))}
    ds:=&appsv1.DeploymentList{};if err:=b.Reader.List(ctx,ds,selectors...);err!=nil{return false,err}
    if len(ds.Items)>0{
        for i:=range ds.Items{d:=&ds.Items[i];if !ownedBy(d,owner){return false,fmt.Errorf("foreign matching Deployment")};if d.DeletionTimestamp.IsZero(){if err:=b.deleteOwned(ctx,d,owner);err!=nil{return false,err}}}
        return false,nil
    }
    pods,err:=b.pods(ctx,owner,role);if err!=nil{return false,err}
    if len(pods)>0{return false,nil}
    objects:=[]client.Object{}
    services:=&corev1.ServiceList{};if err=b.Reader.List(ctx,services,selectors...);err!=nil{return false,err};for i:=range services.Items{objects=append(objects,&services.Items[i])}
    policies:=&netv1.NetworkPolicyList{};if err=b.Reader.List(ctx,policies,selectors...);err!=nil{return false,err};for i:=range policies.Items{objects=append(objects,&policies.Items[i])}
    configs:=&corev1.ConfigMapList{};if err=b.Reader.List(ctx,configs,selectors...);err!=nil{return false,err};for i:=range configs.Items{objects=append(objects,&configs.Items[i])}
    for _,o:=range objects{if o.GetDeletionTimestamp().IsZero(){if err=b.deleteOwned(ctx,o,owner);err!=nil{return false,err}}}
    return len(objects)==0,nil
}
func (b *Base) revoke(ctx context.Context,w *api.FlytWorker,cp *api.FlytControlPlane)error{
    var unavailable error
    if cp!=nil{
        _,unavailable=b.binding(ctx,cp,map[string]interface{}{"op":"unbind","worker_uid":string(w.UID)})
        if unavailable==nil{return nil}
    }
    // A missing/recreated control-plane CR is not proof that its old manager is
    // dead. Keep cleanup pending while any old manager Pod object still exists.
    pods:=&corev1.PodList{}
    if err:=b.Reader.List(ctx,pods,client.InNamespace(w.Namespace),client.MatchingLabels{PlaneLabel:w.Spec.ControlPlaneRef.UID,"flyt.dev/role":"manager",Experiment:Managed});err!=nil{return err}
    if len(pods.Items)>0{return fmt.Errorf("manager unavailable while its Pods still exist: %v",unavailable)}
    // No manager Pod objects means no surviving in-memory binding. This also
    // permits deleting a Worker whose manager was never created. Any future
    // manager starts empty; this reconciler will not bind a terminating Worker.
    return nil
}
